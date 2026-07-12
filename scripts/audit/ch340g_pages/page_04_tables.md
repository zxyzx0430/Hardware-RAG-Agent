|Pin #|Name|Direction|Comment|
|---|---|---|---|
|1|GND|Power|Ground reference of the chip. Connect to the ground pin of USB bus.|
|2|TXD|Output|UART Data Transmit output.|
|3|RXD|Input|UART Data Receive input.|
|4|V3|Power|Internal 3.3V reference for USB physical layer. Decouple with a 4.7-20nF<br>capacitor when in 5V operation, or tie to VCC when in 3.3V operation.|
|5|UD+|Analog|USB D+ signal.|
|6|UD-|Analog|USB D- signal.|
|7|XI|Input|Input of the crystal oscillator. Connect to the crystal resonator and load<br>capacitors.|
|8|XO|Output|Output of the crystal oscillator. Connect to the crystal resonator and load<br>capacitors.|
|9|CTS#|Input|UART flow control signal Clear to Send.|
|10|DSR#|Input|UART flow control signal Data Set Ready.|
|11|RI#|Input|UART flow control signal Ring In.|
|12|DCD#|Input|UART flow control signal Data Carrier Detect.|
|13|DTR#|Output|UART flow control signal Data Terminal Ready.|
|14|RTS#|Output|UART flow control signal Request to Send.|
|15|R232|Input|Auxiliary RS232 enable. Active high, internal pull down.|
|16|VCC|Power|Supply rail for the chip.|