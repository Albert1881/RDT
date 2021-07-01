import random
import struct
from enum import Enum

from USocket import UnreliableSocket
import threading
import time
import ctypes
import inspect
import asyncio
import logging


# State enumeration class
class STATE(Enum):
    OPENED = 0
    CONNECTING = 1
    CONNECTING_ACK = 2
    LISTENING = 3
    CONNECTED = 4

    SEND = 5
    SEND_RCV = 6
    RESEND = 7
    UPDATE_CWDN = 8

    RCV = 9
    RCV_ACK = 10

    CLOSING = 11
    CLOSED = 12


# message class
class Payload(object):
    def __init__(self, syn=0, fin=0, ack=0, seq=0, seq_ack=0, data=b''):
        self.SYN = syn
        self.FIN = fin
        self.ACK = ack
        self.SEQ = seq
        self.SEQ_ACK = seq_ack
        self.CHECKSUM = 0
        self.data = data
        self.p_payload = b''
        self.corrupt = False

    def set_syn(self, syn):
        self.SYN = syn

    def set_fin(self, fin):
        self.FIN = fin

    def set_ack(self, ack):
        self.SYN = ack

    def set_seq(self, seq):
        self.SEQ = seq

    def set_seq_ack(self, seq_ack):
        self.SEQ_ACK = seq_ack

    def set_data(self, data):
        self.data = data

    def get_len(self):
        return len(self.data)

    # payload undecode
    def unpack_payload(self, p_payload):
        if len(p_payload) < 16:
            return False
        header = p_payload[0:16]
        flags, self.SEQ, self.SEQ_ACK, LEN, check_sum = struct.unpack('!HLLLH', header)

        self.SYN = flags % 2
        self.FIN = (flags // 2) % 2
        self.ACK = (flags // 4) % 2
        self.data = p_payload[16:]
        self.corrupt = not (check_sum == self.calc_checksum())
        return True

    # payload decode
    def pack_payload(self):
        flags = self.SYN + (self.FIN << 1) + (self.ACK << 2)
        payload_len = self.get_len()
        check_sum = self.calc_checksum()
        self.p_payload = struct.pack('!HLLLH{}s'.format(payload_len), flags, self.SEQ, self.SEQ_ACK, payload_len,
                                     check_sum,
                                     self.data)

        return self.p_payload

    def calc_checksum(self):
        check_sum = 0
        flags = self.SYN + (self.FIN << 1) + (self.ACK << 2)
        header = struct.pack('!HLLL', flags, self.SEQ, self.SEQ_ACK, self.get_len())
        for byte in header:
            check_sum += byte
            check_sum = -(check_sum % 256)

        for byte in self.data:
            check_sum += byte
            check_sum = -(check_sum % 256)
        check_sum = (check_sum & 0xFF)
        return check_sum

    def __repr__(self):
        return 'SEQ:{} ACK:{} LEN:{}'.format(self.SEQ, self.SEQ_ACK, self.get_len())


class RDTSocket(UnreliableSocket):
    """
    The functions with which you are to build your RDT.
    -   recvfrom(bufsize)->bytes, addr
    -   sendto(bytes, address)
    -   bind(address)

    You can set the mode of the socket.
    -   settimeout(timeout)
    -   setblocking(flag)
    By default, a socket is created in the blocking mode. 
    https://docs.python.org/3/library/socket.html#socket-timeouts

    """

    def __init__(self, rate=None, debug=True):
        super().__init__(rate=rate)
        self._rate = rate
        self._send_to = None
        self._recv_from = None
        self.debug = debug
        #############################################################################
        # TODO: ADD YOUR NECESSARY ATTRIBUTES HERE
        #############################################################################
        if debug:
            logging.basicConfig(format='%(asctime)s - %(pathname)s[line:%(lineno)d] - %(levelname)s: %(message)s',
                                level=logging.DEBUG)
        self.timer = None
        self.RESEND_TIMES = 13

        self.a = 0.125
        self.b = 0.25
        self.EstimatedRTT = 0.1
        self.DevRTT = 0.01
        self.TIMEOUT = self.EstimatedRTT + 4 * self.DevRTT

        self.state = STATE.OPENED
        self.send_state = STATE.CONNECTED
        self.recv_state = STATE.CONNECTED

        self.SEQ_NUM = random.randint(0, 100)
        self.ACK_NUM = 0
        self.SendBase = -1

        self.BUFFER_SIZE = 1024000
        self.PLD_SIZE = 1024
        self.sndlist = []

        self.recvlist = []
        for i in range(self.BUFFER_SIZE // self.PLD_SIZE):
            self.recvlist.append(None)
        self.recvdata = b''
        self.recvack_list = []
        self.recvdata_list = []
        self.cwdn = 1
        self.ssthresh = 4

        self.DEST_CLOSED = False
        self.recv_thread = TimerThread(target=self.get_recvlist)
        self.recv_thread.start()
        self.send_threadlist = []


        #############################################################################
        #                             END OF YOUR CODE                              #
        #############################################################################

    def accept(self):
        """
        accept(self) -> (RDTSocket, (str, int)
        Accept a connection. The socket must be bound to an address and listening for
        connections. The return value is a pair (conn, address) where conn is a new
        socket object usable to send and receive data on the connection, and address
        is the address bound to the socket on the other end of the connection.

        This function should be blocking.
        """
        conn, addr = RDTSocket(rate=self._rate, debug=self.debug), None
        #############################################################################
        # TODO: YOUR CODE HERE                                                      #
        #############################################################################
        self.recv_check = True
        while True:
            try:
                addr = (self.getsockname()[0], random.randrange(32000, 62000, 1))
                conn.bind(addr)
                break
            except Exception as e:
                logging.info(str(e))

        acc_thread = TimerThread(self.accept_FSM, args=(conn,))
        acc_thread.start()
        acc_thread.join()

        logging.info('Server connect client {}'.format(conn._recv_from))
        logging.info("conn.SEQ_NUM : " + str(conn.SEQ_NUM))
        logging.info("conn.ACK_NUM : " + str(conn.ACK_NUM))

        #############################################################################
        #                             END OF YOUR CODE                              #
        #############################################################################
        return conn, addr

    def accept_FSM(self, conn):
        resend_num = 0
        conn.SendBase = conn.SEQ_NUM
        ackBase = 0
        startRTT = -1
        while conn.state != STATE.CONNECTED:
            logging.info(conn.state)
            # send syn payload
            if conn.state == STATE.OPENED:
                syn_payload = None
                # Continue the call until the package is received or a timeout occurs
                end = time.time() + conn.TIMEOUT
                while syn_payload is None and time.time() <= end:
                    syn_payload, _ = self.get_recvpld(ack=0)
                if syn_payload and syn_payload.SYN == 1:
                    conn.set_recv_from(_)
                    conn.set_send_to(_)
                    ackBase = syn_payload.SEQ
                    conn.ACK_NUM = ackBase + 1
                    conn.state = STATE.CONNECTING_ACK
                    logging.info("Server receives syn packet {}".format(syn_payload.SEQ))

            # recv synack payload
            elif conn.state == STATE.CONNECTING_ACK:
                conn.SEQ_NUM = conn.SendBase
                synack_payload = Payload(syn=1, ack=1, seq=conn.SEQ_NUM, seq_ack=conn.ACK_NUM)
                conn.sendto(synack_payload.pack_payload(), conn._send_to)
                conn.SEQ_NUM += 1
                conn.state = STATE.LISTENING
                startRTT = time.time()
                logging.info("Server send synack packet {}".format(synack_payload.SEQ))

            # recv ack payload
            elif conn.state == STATE.LISTENING:
                ack_payload = None
                end = time.time() + conn.TIMEOUT
                while ack_payload is None and time.time() <= end:
                    ack_payload, _ = conn.get_recvpld(ack=1, address=conn._recv_from)

                if ack_payload is None:
                    conn.updateTimeoutlnterval()
                    startRTT = -1
                    resend_num += 1
                    if resend_num >= conn.RESEND_TIMES:
                        conn.state = STATE.CONNECTED
                    else:
                        conn.state = STATE.CONNECTING_ACK
                    continue
                if startRTT > 0:
                    conn.updateTimeoutlnterval(time.time() - startRTT)
                    startRTT = -1
                resend_num = 0
                if ack_payload.ACK == 1 and ack_payload.SYN != 1 and ack_payload.SEQ == conn.ACK_NUM:
                    logging.info("Server receve ack packet {}".format(ack_payload.SEQ))

                    conn.state = STATE.CONNECTED
                    conn.ACK_NUM = ackBase + 2
                else:
                    if resend_num >= conn.RESEND_TIMES:
                        conn.state = STATE.CONNECTED
                    else:
                        conn.state = STATE.CONNECTING_ACK
                    continue

    def connect(self, address: (str, int)):
        """
        Connect to a remote socket at address.
        Corresponds to the process of establishing a connection on the client side.
        """
        #############################################################################
        # TODO: YOUR CODE HERE                                                      #
        #############################################################################
        conn_thread = TimerThread(self.connect_FSM, args=(address,))
        conn_thread.start()
        conn_thread.join()
        logging.info('Client connect server {}'.format(self._send_to))

        logging.info("client.SEQ_NUM : " + str(self.SEQ_NUM))
        logging.info("client.ACK_NUM : " + str(self.ACK_NUM))
        #############################################################################
        #                             END OF YOUR CODE                              #
        #############################################################################

    def connect_FSM(self, address):
        if address is None:
            return None
        resend_num = 0
        self.SendBase = self.SEQ_NUM
        startRTT = -1
        while (self.state != STATE.CONNECTED):
            logging.info(self.state)

            # recv fin payload
            if self.state == STATE.OPENED:
                self.SEQ_NUM = self.SendBase
                syn_pld = Payload(syn=1, ack=0, seq=self.SEQ_NUM)
                self.sendto(syn_pld.pack_payload(), address)
                self.state = STATE.CONNECTING
                self.SEQ_NUM += 1
                logging.info('Client sends syn packet {}'.format(syn_pld.SEQ))
                startRTT = time.time()

            # send finack payload
            elif self.state == STATE.CONNECTING:
                synack_payload = None
                end = time.time() + self.TIMEOUT
                while synack_payload is None and time.time() <= end:
                    synack_payload, _ = self.get_recvpld(ack=1, address=(address[0], None))

                if synack_payload is None:
                    self.state = STATE.OPENED
                    resend_num += 1
                    self.updateTimeoutlnterval()
                    startRTT = -1
                    if resend_num >= self.RESEND_TIMES:
                        return None
                    else:
                        continue
                # Estimated RTT
                if startRTT > 0:
                    self.updateTimeoutlnterval(time.time() - startRTT)
                    startRTT = -1
                if synack_payload.SYN != 1 or synack_payload.ACK != 1:
                    self.state = STATE.OPENED
                    resend_num += 1
                    if resend_num >= self.RESEND_TIMES:
                        return None
                    else:
                        continue
                else:
                    logging.info('Client receives synack packet {}'.format(synack_payload.SEQ))

                    self.set_send_to(_)
                    self.set_recv_from(_)
                    self.ACK_NUM = synack_payload.SEQ + synack_payload.get_len() + 1
                    ack_payload = Payload(seq=self.SEQ_NUM, ack=1, seq_ack=self.ACK_NUM)
                    for i in range(self.RESEND_TIMES):
                        self.sendto(ack_payload.pack_payload(), self._send_to)
                    self.SEQ_NUM = self.SendBase + 2
                    self.state = STATE.CONNECTED
                    logging.info('Client sends ack {}'.format(ack_payload.SEQ))


    def recv(self, bufsize: int) -> bytes:
        """
        Receive data from the socket.
        The return value is a bytes object representing the data received.
        The maximum amount of data to be received at once is specified by bufsize.

        Note that ONLY data send by the peer should be accepted.
        In other words, if someone else sends data to you from another address,
        it MUST NOT affect the data returned by this function.
        """
        data = None
        assert self._recv_from, "Connection not established yet. Use recvfrom instead."
        #############################################################################
        # TODO: YOUR CODE HERE                                                      #
        #############################################################################
        RcvBase = self.ACK_NUM
        if self.state != STATE.CONNECTED or self.DEST_CLOSED:
            return None

        self.recv_state = STATE.RCV_ACK
        resend_num = 0
        finish = False  # Whether the reception is over
        startRTT = -1

        # Update ACK_NUM if the RcvBase location has data
        while self.recvlist[0] and self.recvlist[0].data != b'\0' and len(self.recvdata) + self.recvlist[
            0].get_len() <= bufsize:
            self.recvdata += self.recvlist[0].data
            self.ACK_NUM += self.recvlist[0].get_len()
            RcvBase = self.ACK_NUM
            self.recvlist.pop(0)
            self.recvlist.append(None)
        # b'\0' special payloay, represends send end
        if self.recvlist[0] and self.recvlist[0].data == b'\0':
            self.ACK_NUM += self.recvlist[0].get_len()
            RcvBase = self.ACK_NUM
            self.recvlist.pop(0)
            self.recvlist.append(None)
            finish = True
        elif self.recvlist[0] and len(self.recvdata) + self.recvlist[0].get_len() > bufsize:
            finish = True

        while self.recv_state != STATE.CONNECTED:
            logging.info('Recv: {}'.format(self.recv_state))
            # receive data payload
            if self.recv_state == STATE.RCV:
                recv_payload = None
                end = time.time() + self.TIMEOUT
                while recv_payload is None and time.time() <= end:
                    if self.DEST_CLOSED:
                        return None
                    recv_payload, _ = self.get_recvpld(ack=0, address=self._recv_from)

                if recv_payload is None:
                    resend_num += 1
                    self.recv_state = STATE.RCV_ACK
                    self.updateTimeoutlnterval()
                    startRTT = -1
                    continue
                resend_num = 0

                logging.info(
                    "R: Receive packet SEQ: {}, ACK: {}, data: {}".format(recv_payload.SEQ, recv_payload.SEQ_ACK,
                                                                          recv_payload.data))
                if startRTT > 0:
                    self.updateTimeoutlnterval(time.time() - startRTT)
                    startRTT = -1

                rcv_index = (recv_payload.SEQ + self.PLD_SIZE - 1 - RcvBase) // self.PLD_SIZE

                if recv_payload.SEQ >= self.ACK_NUM and rcv_index < len(self.recvlist):
                    self.recvlist[rcv_index] = recv_payload

                # Update ACK_NUM if the RcvBase location has data
                if self.ACK_NUM == recv_payload.SEQ:
                    while self.recvlist[0] is not None and self.recvlist[0].data != b'\0' and len(self.recvdata) + \
                            self.recvlist[0].get_len() <= bufsize:
                        self.recvdata += self.recvlist[0].data
                        self.ACK_NUM += self.recvlist[0].get_len()
                        RcvBase = self.ACK_NUM
                        self.recvlist.pop(0)
                        self.recvlist.append(None)
                    if self.recvlist[0] and self.recvlist[0].data == b'\0':
                        self.ACK_NUM += self.recvlist[0].get_len()
                        RcvBase = self.ACK_NUM
                        self.recvlist.pop(0)
                        self.recvlist.append(None)
                        finish = True
                    elif self.recvlist[0] and len(self.recvdata) + self.recvlist[0].get_len() > bufsize:
                        finish = True
                self.recv_state = STATE.RCV_ACK
            # send ack payload
            elif self.recv_state == STATE.RCV_ACK:
                ack_payload = Payload(ack=1, seq=self.SEQ_NUM, seq_ack=self.ACK_NUM)
                self.sendto(ack_payload.pack_payload(), self._send_to)
                startRTT = time.time()
                logging.info("R: Send ACK packet {}".format(ack_payload.SEQ_ACK))

                if finish or self.DEST_CLOSED or (
                        resend_num >= self.RESEND_TIMES and self.TIMEOUT >= self.EstimatedRTT * pow(2,
                                                                                                    self.RESEND_TIMES)):
                    self.recv_state = STATE.CONNECTED
                else:
                    self.recv_state = STATE.RCV

        if self.recvlist[0] and len(self.recvdata) < bufsize:
            data = self.recvdata
            data += self.recvlist[0].data[0:bufsize - len(self.recvdata)]
            self.recvdata = self.recvlist[0].data[bufsize - len(self.recvdata):]
            self.ACK_NUM += self.recvlist[0].get_len()
            self.recvlist.pop(0)
            self.recvlist.append(None)
            while self.recvlist[0].data == b'\0':
                self.ACK_NUM += self.recvlist[0].get_len()
                self.recvlist.pop(0)
                self.recvlist.append(None)
        elif len(self.recvdata) > 0:
            data = self.recvdata
            self.recvdata = b''

        logging.info("self.SEQ_NUM : " + str(self.SEQ_NUM))
        logging.info("self.ACK_NUM : " + str(self.ACK_NUM))
        #############################################################################
        #                             END OF YOUR CODE                              #
        #############################################################################
        return data

    def send(self, bytes: bytes):
        """
        Send data to the socket.
        The socket must be connected to a remote socket, i.e. self._send_to must not be none.
        """
        assert self._send_to, "Connection not established yet. Use sendto instead."
        #############################################################################
        # TODO: YOUR CODE HERE                                                      #
        #############################################################################

        self.send_threadlist.append(TimerThread(self.send_FSM, args=(bytes,)))
        if len(self.send_threadlist) == 1:
            self.send_threadlist[0].start()
        #############################################################################
        #                             END OF YOUR CODE                              #
        #############################################################################

    def send_FSM(self, bytes):
        if self.state != STATE.CONNECTED or self.DEST_CLOSED:
            return None
        self.send_state = STATE.SEND
        self.SendBase = self.SEQ_NUM
        self.NextSegNum = self.SEQ_NUM

        # data section
        pld_num = (len(bytes) + self.PLD_SIZE - 1) // self.PLD_SIZE
        for i in range(0, pld_num):
            data_slice = bytes[i * self.PLD_SIZE: (i + 1) * self.PLD_SIZE]
            payload = Payload(seq=self.SEQ_NUM, seq_ack=self.ACK_NUM, data=data_slice)
            self.sndlist.append([payload, 0])
            self.SEQ_NUM += payload.get_len()
        # special end payload
        payload = Payload(seq=self.SEQ_NUM, seq_ack=self.ACK_NUM, data=b'\0')
        self.sndlist.append([payload, 0])
        self.SEQ_NUM += payload.get_len()

        resend_count = 0
        MSS = 0
        startRTT = -1
        while self.send_state != STATE.CONNECTED:
            logging.info('Send: {}'.format(self.send_state))
            # send data payload
            if self.send_state == STATE.SEND:
                start = (self.NextSegNum - self.SendBase + self.PLD_SIZE - 1) // self.PLD_SIZE
                end = min(len(self.sndlist), self.cwdn)

                for i in range(start, end):
                    self.sendto(self.sndlist[i][0].pack_payload(), self._send_to)
                    logging.info(
                        "S: Sends packet {}, data {}".format(self.sndlist[i][0].SEQ, self.sndlist[i][0].data))
                    self.NextSegNum += self.sndlist[i][0].get_len()
                if start >= end:
                    self.sndlist[0][1] += 1
                startRTT = time.time()
                self.send_state = STATE.SEND_RCV
            # receive ack
            elif self.send_state == STATE.SEND_RCV:
                recv_payload = None
                end = time.time() + self.TIMEOUT
                while recv_payload is None and time.time() <= end:
                    recv_payload, _ = self.get_recvpld(ack=1, address=self._recv_from)
                # time out
                if recv_payload is None:
                    self.send_state = STATE.RESEND
                    self.cwdn = 1
                    self.ssthresh = max(1, min(len(self.sndlist), self.cwdn // 2))
                    self.updateTimeoutlnterval()
                    startRTT = -1
                    continue
                logging.info("Receive ack payload SEQ: {}, ACK: {}".format(recv_payload.SEQ, recv_payload.SEQ_ACK))
                resend_count = 0

                # update cwdn
                if self.cwdn < self.ssthresh:
                    self.cwdn = min(self.cwdn + 1, len(self.sndlist))
                elif MSS >= self.cwdn:
                    self.cwdn = min(self.cwdn + 1, len(self.sndlist))
                    MSS = 0
                else:
                    MSS += 1
                if startRTT > 0:
                    self.updateTimeoutlnterval(time.time() - startRTT)
                    startRTT = -1
                # Fast retransmission
                if self.sndlist[0][0].SEQ == recv_payload.SEQ_ACK:
                    self.sndlist[0][1] += 1
                    if self.sndlist[0][1] >= 3:
                        self.sendto(self.sndlist[0][0].pack_payload(), self._send_to)
                        logging.info("Fast retransmission: sends packet {}".format(self.sndlist[0][0].SEQ))
                        self.sndlist[0][1] = 2
                # update send base
                elif self.sndlist[0][0].SEQ < recv_payload.SEQ_ACK:
                    while self.sndlist and self.sndlist[0][0].SEQ < recv_payload.SEQ_ACK:
                        self.SendBase += self.sndlist[0][0].get_len()
                        self.sndlist.pop(0)
                    if self.SendBase > self.NextSegNum:
                        self.NextSegNum = self.SendBase
                    logging.info('Base update {}'.format(self.SendBase))
                # send over
                if not self.sndlist:
                    self.send_state = STATE.CONNECTED
                else:
                    self.send_state = STATE.SEND
            # timeout, resend payload
            elif self.send_state == STATE.RESEND:
                resend_count += 1
                if resend_count >= self.RESEND_TIMES and self.TIMEOUT >= self.EstimatedRTT * pow(2,
                                                                                                 self.RESEND_TIMES):
                    self.send_state = STATE.CONNECTED
                    logging.info("Resent fail!")
                    return None
                logging.info("Time out! Resend the packets from {} to {}".format(self.SendBase, self.NextSegNum))

                resnd_num = min(self.cwdn, (self.NextSegNum + self.PLD_SIZE - 1 - self.SendBase) // self.PLD_SIZE)
                for i in range(0, resnd_num):
                    self.sendto(self.sndlist[i][0].pack_payload(), self._send_to)
                    logging.info("Resend packet {}".format(self.sndlist[i][0].SEQ))
                startRTT = time.time()
                self.send_state = STATE.SEND_RCV

        logging.info("self.SEQ_NUM : " + str(self.SEQ_NUM))
        logging.info("self.ACK_NUM : " + str(self.ACK_NUM))
        self.send_threadlist.pop(0)
        if self.send_threadlist:
            self.send_threadlist[0].start()

    def close(self):
        """
        Finish the connection and release resources. For simplicity, assume that
        after a socket is closed, neither futher sends nor receives are allowed.
        """
        #############################################################################
        # TODO: YOUR CODE HERE                                                      #
        #############################################################################
        resend = 0

        while self.send_threadlist:
            time.sleep(1)
        time.sleep(self.TIMEOUT)
        self.SendBase = self.SEQ_NUM
        while self._send_to and self.state != STATE.CLOSED:
            # send fin payload
            if self.state == STATE.CONNECTED:
                self.SEQ_NUM = self.SendBase
                finpld = Payload(fin=1, seq=self.SEQ_NUM, seq_ack=self.ACK_NUM)
                self.sendto(finpld.pack_payload(), self._send_to)
                logging.info("CLOSING: Send packet {}".format(finpld.SEQ))
                self.SEQ_NUM += 1
                resend += 1
                if resend >= self.RESEND_TIMES:
                    self.state = STATE.CLOSED
                else:
                    self.state = STATE.CLOSING
            # receive ack
            elif self.state == STATE.CLOSING:
                recv_payload = None
                end = time.time() + self.TIMEOUT
                while recv_payload is None and time.time() < end and not self.DEST_CLOSED:
                    recv_payload, _ = self.get_recvpld(ack=1, address=self._recv_from)

                if self.DEST_CLOSED:
                    self.state = STATE.CLOSED
                    continue
                elif recv_payload is None:
                    self.state = STATE.CONNECTED
                    continue

                if recv_payload.SEQ_ACK == self.SEQ_NUM and recv_payload.ACK == 1:
                    logging.info("CLOSED: Receve ack packet {}".format(recv_payload.SEQ_ACK))
                    self.state = STATE.CLOSED
        logging.info("conn closed!")
        if self.recv_thread.is_alive():
            self.recv_thread.stop()

        #############################################################################
        #                             END OF YOUR CODE                              #
        #############################################################################
        super().close()

    def set_send_to(self, send_to):
        self._send_to = send_to

    def set_recv_from(self, recv_from):
        self._recv_from = recv_from

    # The receiving data thread drops the receiving message into list
    def get_recvlist(self):
        while True:
            try:
                pack_recvpld, _ = self.recvfrom(self.BUFFER_SIZE)
                if pack_recvpld:
                    recvpld = Payload()
                    recvpld.unpack_payload(pack_recvpld)
                    if not recvpld.corrupt:
                        if recvpld.ACK == 1:
                            self.recvack_list.append([recvpld, _, time.time() + self.TIMEOUT])
                        else:
                            if recvpld.SEQ < self.ACK_NUM:
                                ackpld = Payload(seq=self.SEQ_NUM, seq_ack=self.ACK_NUM, ack=1)
                                self.sendto(ackpld.pack_payload(), _)
                                logging.info("ACK: Send ack packet SEQ {}, ACK {}".format(ackpld.SEQ, ackpld.SEQ_ACK))
                            elif recvpld.FIN == 1:
                                self.DEST_CLOSED = True
                                if self.state == STATE.CONNECTED:
                                    finackpld = Payload(seq=self.SEQ_NUM, seq_ack=self.ACK_NUM + 1, ack=1)
                                else:
                                    finackpld = Payload(seq=self.SEQ_NUM, seq_ack=self.ACK_NUM + 1, ack=0)
                                self.sendto(finackpld.pack_payload(), _)
                                logging.info("CLOSING: Send finack packet {}".format(finackpld.SEQ))
                            else:
                                self.recvdata_list.append([recvpld, _, time.time() + self.TIMEOUT])

            except OSError:
                pass

    # get the receiving message from list
    def get_recvpld(self, ack, address=None):
        recvpld = None
        _ = None
        if ack == 1:
            for recvinfo in self.recvack_list[:]:
                if time.time() > recvinfo[2]:
                    self.recvack_list.remove(recvinfo)
                elif address and address[0] == recvinfo[1][0]:
                    if address[1] is None or address[1] == recvinfo[1][1]:
                        recvpld = recvinfo[0]
                        _ = recvinfo[1]
                        self.recvack_list.remove(recvinfo)
                        break
                elif address is None:
                    recvpld = recvinfo[0]
                    _ = recvinfo[1]
                    self.recvack_list.remove(recvinfo)
                    break
        else:
            for recvinfo in self.recvdata_list[:]:
                if time.time() > recvinfo[2]:
                    self.recvdata_list.remove(recvinfo)
                elif address and address[0] == recvinfo[1][0]:
                    if address[1] is None or address[1] == recvinfo[1][1]:
                        recvpld = recvinfo[0]
                        _ = recvinfo[1]
                        self.recvdata_list.remove(recvinfo)
                        break
                elif address is None:
                    recvpld = recvinfo[0]
                    _ = recvinfo[1]
                    self.recvdata_list.remove(recvinfo)
                    break
        return recvpld, _

    # update timeout interval
    def updateTimeoutlnterval(self, SampleRTT=None):

        if SampleRTT:
            if SampleRTT > 5 * self.EstimatedRTT:
                return
            self.EstimatedRTT = (1 - self.a) * self.EstimatedRTT + self.a * SampleRTT
            self.DevRTT = (1 - self.b) * self.DevRTT + abs(SampleRTT - self.EstimatedRTT)
            self.TIMEOUT = self.EstimatedRTT + 4 * self.DevRTT
        else:
            self.TIMEOUT *= 2


"""
You can define additional functions and classes to do thing such as packing/unpacking packets, or threading.

"""


class TimerThread(threading.Thread):

    def __init__(self, target, args=()):
        super(TimerThread, self).__init__(target=target, args=args)

    def stop(self):
        """raises the exception, performs cleanup if needed"""
        exctype = SystemExit
        tid = ctypes.c_long(self.ident)
        if not inspect.isclass(exctype):
            exctype = type(exctype)
        res = ctypes.pythonapi.PyThreadState_SetAsyncExc(tid, ctypes.py_object(exctype))
        if res == 0:
            raise ValueError("invalid thread id")
        elif res != 1:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(tid, None)
            raise SystemError("PyThreadState_SetAsyncExc failed")
