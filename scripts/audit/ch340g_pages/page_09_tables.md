|Col1|Col2|Col3|Col4|Col5|pull-up resistor|
|---|---|---|---|---|---|
|10|8|NONE|XO|OUT|CH340T/R/G: Output of crystal oscillator, connect to<br>crystal and capacitor|
|10|8|NONE|NC.|NONE|CH340C/B: No Connection, must be suspended|
|6|5|1|UD+|USB signal|Directly connect to D+ data wire of USB bus|
|7|6|2|UD-|USB signal|Directly connect to D- data wire of USB bus|
|20|NONE|NONE|NOS#|IN|Forbid USB device suspending, active low, built-in pull-up<br>resistor|
|3|2|8|TXD|OUT|Transmit asynchronous data output(reverse output for<br>CH340R)|
|4|3|9|RXD|IN|Receive asynchronous data input, built-in configurable<br>pull-up andpull-down resistor|
|11|9|5|CTS#|IN|MODEM liaison input signal, clear to send, active<br>low(high)|
|12|10|NONE|DSR#|IN|MODEM liaison input signal, data set ready, active<br>low(high)|
|13|11|NONE|RI#|IN|MODEM liaison input signal, ring indicator , active<br>low(high)|
|14|12|NONE|DCD#|IN|MODEM liaison input signal, data carrier detect, active<br>low(high)|
|15|13|NONE|DTR#|OUT|MODEM liaison output signal, data terminal ready, active<br>low(high)|
|16|14|4|RTS#|OUT|MODEM liaison output signal, request to send, active<br>low(high)|
|2|NONE|NONE|ACT#|OUT|USB configuration completed state output, active low|
|18|15|NONE|R232|IN|CH340T/R/G/C: Assistant RS232 enable, active high,<br>built-inpull-down resistor|
|17|15|6|TNOW|OUT|CH340T/E/B: Ongoing data transmission status indicator,<br>active high|
|17|15|6|IR#|IN|CH340R:Serial mode input setting, built-in pull-up resistor,<br>SIR infrared serial interface when low, common serial<br>interface when high|
|1|NONE|NONE|CK0|OUT|CH340T: clock output|
|1|NONE|NONE|NC.|NONE|CH340R:No Connection, must be suspend|