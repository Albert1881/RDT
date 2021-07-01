# Reliable Data Transfer

Project for CS305: Reliable Data Transfer.

This project is based on the existing UDP socket to achieve a reliable data transmission protocol, and with a packet loss rate of 10%, the rate reaches 60%. This project mainly builds a finite state machine, defines the different states of the socket, simulates the TCP protocol to achieve three-way handshake, four waved hands, and retransmission mechanism; at the same time, set the timeout time by estimating the round-trip time of the data packet, and modify the sliding window size To achieve congestion control;



## Principle

### Connection

- Process

<img src="images\Process_Connect&Accept.png" alt="image-20210702010446304" style="zoom:33%;" />



<img src="C:\Users\albert\AppData\Roaming\Typora\typora-user-images\image-20210702010607779.png" alt="image-20210702010607779" style="zoom:33%;" />

- Flow chart

  <img src="images\FlowChart_Connect&Close.png" alt="image-20210702011124093" style="zoom:50%;" />

- Log

  - Connect

  <img src="images\Log_Connect.png" alt="image-20210702011403576" style="zoom:33%;" />

  - Close

  <img src="images\Log_Close.png" alt="image-20210702011429011" style="zoom:33%;" />

### Data Transmission

- Process
  - Send (Like GBN)

    - Send payloads from nextseqnum to base + N

    - Resend payloads from base to nextseqnum

      <img src="images\Process_Send.png" alt="image-20210702011509092" style="zoom:33%;" />

  - Receive	

    - Receive individual payload in rcv_base to rcv_base + N (Like SR)

    - send accumulate ACK payload of rcv_base (Like GBN)

      <img src="images\Process_Receive.png" alt="image-20210702011543270" style="zoom:33%;" />

- Flow chart

  - Send

    <img src="images\FlowChart_Send.png" alt="image-20210702011821174" style="zoom:50%;" />

  - Receive

    <img src="images\FlowChart_Receive.png" alt="image-20210702011803200" style="zoom: 50%;" />

- Log

  <img src="images\Log_Send&Receive.png" alt="image-20210702011915811" style="zoom:50%;" />

### Congestion Control

- Process

  - CWDN

    - Slow Start: 
      - cwdn = 1 (Time out)
      - cwdn = cwdn * 2
    - Congestion Avoidance:
      - cwdn = cwdn + 1

  - Fast Retransmit

    <img src="images\Process_CWDN.png" alt="image-20210702012038169" style="zoom:33%;" />

  - Timeout Interval 

    - Receive payload:
      - TimeoutInterval = EstimatedRTT + 4 * DevRTT
      - EstimatedRTT = (1 - α) * EstimatedRTT + α * SampleRTT
      - DevRTT = (1 - β) * DevRTT + β * |SampleRTT – EstimatedRTT|
    - Time out
      - Doubling the Timeout Interval

- Flow chart

  - Congestion Control

    <img src="images\FlowChart_Control.png" alt="image-20210702012158339" style="zoom:50%;" />

- Pseudocode

  - When Timeout:

    ```
    ssthresh = cwnd / 2 
    cwnd = 1 
    dupACKcount = 0
    RESEND
    ```

  - When duplicate ACK:

    ```
    dupACKcount++
    if dupACKcount = 3 :
    	ssthresh = cwnd/2
    	Resend missing segment
    ```

  - When new ACK:

    ```
    if cwdn < ssthresh:
    	cwnd += 1
    else if ACKcount = cwnd:
    	cwnd += 1
    	ACKcount = 0
    else:
    	ACKcount += 1
    ```

- Log

  <img src="images\Log_CWDN.png" alt="image-20210702012621274" style="zoom:50%;" />

  

### Feature

- In send function, we will add a special payload at the end of the data. When recv function receives this payload, it represents the end of the received data. Causes data from two consecutive send functions not to be received by a single recv functions
- The data sending and receiving mode we adopted is between GBN and SR, with buffer at both receiving end and sending end. And accumulative ACK is adopted to make fast retransmission more effective.





