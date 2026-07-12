WCH CH340 Series USB Interface Integrated Circuit

# CH340G USB to UART Interface Datasheet

WCH Version 1E DreamCity Version 1.0

## Disclaimer from DreamCity Innovations

This is a third-party translation of WCH’s CH340 Series chipset’s Chinese datasheet, with information regarding CH340G chip extracted. The two other chips sharing the same datasheet, CH340R and C340T, have official English version.

## 1. Overview

CH340 is a series of USB bus adapters, that provides serial, parallel or IrDA interfaces over the USB bus (note: CH340G supports serial interface only). The CH340G integrate circuit provides common MODEM signals to allow adding a UART to a computer, or converting existing UART devices to USB interface.

## 2. Features

- • Full-speed USB interface, compatible with USB 2.0 interface.
- • Operates with a minimum amount of external components: a crystal and a minimum of four capacitors.
- • Provides a virtual serial port for upgrading existing serial port devices or adding serial ports to a PC.
- • Supports all existing applications using serial ports without the need of changing existing code.
- • Hardware full-duplex serial interface with internal FIFO. Baud rate range from 50bps to 2Mbps.
- • Supports common flow control signals RTS, DTR, DCD, RI, DSR and CTS.
- • Supports RS232, RS422 and RS485 with external level shifting components.
- • Uses CH341 driver.
- • Supports 5V and 3.3V operation.
- • RoHS-compliant narrow body SO-16 package.


## Table of Contents

##### CH340G USB to UART Interface Datasheet

Disclaimer from DreamCity Innovations 1

- 1. Overview 1
- 2. Features 1 Table of Contents 2
- 3. Specifications 3


- 3.1. Absolute Maximum Ratings 3
- 3.2. DC characteristics 3
- 3.3. AC characteristics 3
- 4. Pinout 4
- 5. Application Notes 4


- 5.1. Example: USB RS232 adapter 5
- 5.2. Example: Optically isolated USB to UART adapter 6


## 3. Specifications

- 3.1. Absolute Maximum Ratings

Operating the chip at or beyond those ratings will cause the chip to malfunction, even irreversibly damage the chip.

- 3.2. DC characteristics
- 3.3. AC characteristics


|Symbol|Name|Minimum|Maximum|Unit|
|---|---|---|---|---|
|TA|Operating temperature|-40|85|ºC|
|TS|Storage temperature|-40|125|ºC|
|VCC|Supply rail voltage, reference to GND pin|-0.5|6.5|V|
|VIO|IO pin voltage, reference to GND pin|-0.5|VCC+0.5|V|


|Symbol|Name| |Minimum|Typical|Maximum|Unit|
|---|---|---|---|---|---|---|
|VCC|Supply rail voltage|5V operation|4.5|5|5.5|V|
| | |3.3V operation|3.3|3.3|3.8| |
|ICC|Operating current| | |12|30|mA|
|ISLP|Sleeping current|5V operation| |150|200|µA|
| | |3.3V operation| |50|80| |
|VIL|Low input voltage| |-0.5| |0.7|V|
|VIH|High input voltage| |2.0| |VCC+0.5|V|
|VOL|Low output voltage| | | |0.5|V|
|VOH|High output voltage| |VCC-0.5| | |V|
|IUP|Internal pull-up strength| |3|150|300|µA|
|IDN|Internal pull-down strength| |-50|-150|-300|µA|
|VR|Brown-out detector threshold voltage| |2.3|2.6|2.9|V|


|Symbol|Name|Minimum|Typical|Maximum|Unit|
|---|---|---|---|---|---|
|FCLK|Clock frequency (at pin XI)|11.98|12.00|12.02|MHz|
|TPR|Power-on reset time| |20|50|ms|


- 4. Pinout
- 5. Application Notes


|Pin #|Name|Direction|Comment|
|---|---|---|---|
|1|GND|Power|Ground reference of the chip. Connect to the ground pin of USB bus.|
|2|TXD|Output|UART Data Transmit output.|
|3|RXD|Input|UART Data Receive input.|
|4|V3|Power|Internal 3.3V reference for USB physical layer. Decouple with a 4.7-20nF capacitor when in 5V operation, or tie to VCC when in 3.3V operation.|
|5|UD+|Analog|USB D+ signal.|
|6|UD-|Analog|USB D- signal.|
|7|XI|Input|Input of the crystal oscillator. Connect to the crystal resonator and load capacitors.|
|8|XO|Output|Output of the crystal oscillator. Connect to the crystal resonator and load capacitors.|
|9|CTS#|Input|UART flow control signal Clear to Send.|
|10|DSR#|Input|UART flow control signal Data Set Ready.|
|11|RI#|Input|UART flow control signal Ring In.|
|12|DCD#|Input|UART flow control signal Data Carrier Detect.|
|13|DTR#|Output|UART flow control signal Data Terminal Ready.|
|14|RTS#|Output|UART flow control signal Request to Send.|
|15|R232|Input|Auxiliary RS232 enable. Active high, internal pull down.|
|16|VCC|Power|Supply rail for the chip.|


CH340 chip have built in USB bus pull-up resistors and on-chip signal termination, UD+ and UD- pins should be connected to the USB bus lines directly.

CH340 have built in power on reset circuitry.

During operation CH340 requires a 12MHz clock signal present at XI pin. Generally this clock signal is provided by connecting a 12MHz crystal resonator and load capacitors between XI and XO pins, and the built-in crystal resonator will provide the required clock signal. When using an external oscillator feed the clock signal into XI pin, and leave XO pin unconnected.

CH340 supports 5V and 3.3V operation. When using 5V operation, supply 5V to VCC pin, and decouple the internal 3.3V reference with a capacitor of 4.7-20nF from V3 pin to ground. When using 3.3V operation, tie V3 pin to VCC pin and supply 3.3V power.

CH340 supports USB device suspension to reduce energy consumption. When NOS# signal is active this feature is disabled. (Note: CH340G does not have this pin.)

Supported hardware flow control signals: CTS#, DSR#, RI#, DCD#, DTR# and RTS#. All flow control pins are software controlled.

Auxiliary pins: IR#, R232, CKO and ACT#. (Note: only R232 is present on CH340G) When R232 signal is asserted the RXD signal is inverted. R232 is latched during Power-On Reset.

CH340 have built-in FIFO buffer, and supports simplex, half- and full-duplex asynchronous communication. The UART interface supports 1 start bit, 5-8 data bits, 1 or 2 stop bits, and odd, even, space or mark parity bits. CH340 supports common baud rates: 50, 75, 100, 110, 134.5, 150, 300, 600, 900, 1200, 1800, 2400, 3600, 4800, 9600, 14400, 19200, 28800, 33600, 38400, 56000, 57600, 76800, 115200, 128000, 153600, 230400, 460800, 921600, 1500000, 2000000 baud. Transmitter baud rate error is less than 0.3%, receiver baud rate error tolerance is at most 2%.

The emulated COM port is fully functional. Compatible with most applications using the serial port, without the need of changing existing code.

CH340 can be used to upgrade existing peripherals using serial ports into USB devices, or adding serial ports to a PC. With external level shifting hardware, interfaces including RS232, RS422 and RS485 can be provided.

By adding infrared adapters, CH340 can be used to implement USB to SIR adapter, allowing a PC to communicate with IrDA peripheral.

### 5.1. Example: USB RS232 adapter

+3V3

+3V3

+5V

+5V

MF-SMDF050

IC2

16

- 2

RXD

- 3


TXD

VCC

TXD

U$1

GND

1

RXD

GND

X1

- 4

XI

7

XO

D+

- 5 D-
- 6


V3

C1 C2

- 1
- 2
- 3
- 4


14 DTR#

RTS

RTS#

###### USB

13 DCD#

D-

DTR

10µ 10n

12 RI#

D+

DCD

Q1

11 DSR#

RI

|8| |
|---|---|
|12|M<br><br>C4|


10 CTS#

DSR

C3

9

CTS

D+

22p 22p

15

PN61729-S

D-

R232

GND

GND

IC1P

+5V

GND

11 10

VCC GND

10µ

C9

100n 100n

IC1

C7 C8

C5

100n 100n

- 14

- R1OUT

8

- R2OUT


- 5

- T1IN

7

- T2IN

6

- T3IN 28




- 20

R5OUT

19

R3IN

27

- T1OUT

- 2

T2OUT

- 3


- T3OUT


1

R5IN

18

C1+

12

R3OUT

26

R2IN

4

R1IN

9

R4IN

23

V-

17

EN

24

SHDN

25

V+

13

T4IN

- 21 T4OUT


C2+

- 15

C2-

- 16


C1-

GND

C6

TXD

H_TXD H_DTR

X2

DTR

- 1

| |6 H_DSR|
|---|---|
| |7 H_RTS|
| |8 H_CTS|
| |9 H_RI|


- 2
- 3
- 4
- 5


RTS

H_RTS H_RXD H_CTS H_DCD

H_DCD

H_RX H_TX

RXD

CTS

H_DTR

DCD

22

RI

H_RI H_DSR

R4OUT

DSR

GND

MAX213ECWI

The schematic above implements a USB to RS232 adapter based on CH340G and external level adapter MAX213. When implementing this regulator-less 5V board, place capacitor C2 close to the V3 pin.

### 5.2. Example: Optically isolated USB to UART adapter

+3V3

+5V

+5V

MF-SMDF050

C2

IC2

10n

16

- 2

RXD

- 3


TXD

VCC

TXD

U$1

GND

1

RXD

GND

X1

- 4

XI

7

XO

D+

- 5 D-
- 6


V3

C1

- 1
- 2
- 3
- 4


14 DTR#

RTS

RTS#

USB

13 DCD#

D-

DTR

10µ

12 RI#

D+

DCD

Q1

11 DSR#

RI DSR CTS

|8| |
|---|---|
|12|M<br><br>C4|


10 CTS#

C3

9

D+

22p 22p

15

PN61729-S

D-

R232

GND

VDD

GND

+5V

- R1
- R2
- R3
- R4
- C5
- R5
- R6
- R7
- R8


OK1

10k

- 3 2
- 4


1

CTS

VDD

IC1G$1

510

4 2

GNDIO

PC817

74LVC1G06DBV

JP1

###### OK2

GNDVCC

GNDVCC

10k

35

35

- 1
- 2 3

4

OK3

1

- 3 2
- 4


GND

IC1G$2

IC3G$2

510

CTS

10µ

TXD

VCC

TXO

VDD

PC817

RXI

10k

DTR

RXD

###### IC3G$1

510

4 2

GNDIO

PC817

74LVC1G06DBV

###### OK4

10k

- 1
- 2 3


4

510

DNP

C6

DTR

PC817

GNDIO

GND

This adapter provides optically isolated inputs and outputs with 4 PC817 optoisolators. Respect the isolation border when laying out the board, and if possible, cut slots under the optoisolators. The two 74LVC1G06 is optional, but helpful when the input pins cannot source too many current.

The Data Sheet of CH340 (the first) 1

#### USB to serial chip CH340

- 1 Introduction

CH340 is a USB bus conversion chip, it can realize USB to UART interface or USB to printer interface. In serial UART mode, CH340 provides common MODEM liaison signal, used to expand UART interface of

computer or upgrade the common serial device to USB bus directly. For more information on USB conversion to printer interface please refer to the manual CH340DS2.

![image 1](<ch340g_datasheet_images/imageFile1.png>)

- 2 Features


English Data Sheet Version: 2C http://wch.cn

- ● Full speed USB device interface, compatible with USB V2.0.
- ● Emulate standard UART interface, used to upgrade the serial peripherals, or expand UART interface via USB bus.
- ● Original serial applications are totally compatible, without any modification in Windows operation system.
- ● Hardware full duplex serial UART interface, built-in transmit-receive buffer, supports communication baud rate varies from 50bps to 2Mbps.
- ● Supports common MODEM liaison signal RTS, DTR, DCD, RI, DSR and CTS.
- ● Through external level conversion chip provide further RS232, RS485, RS422 interface, etc.
- ● CH340R supports IrDA criterion SIR infrared communication, supports baud rate varies from 2400bps to 115200bps.
- ● Software compatible with CH341, use driver of CH341 directly.
- ● Support 5V and 3.3V power supply even 3V.
- ● CH340C, CH340E and CH340B have built-in crystal, no external crystal, CH340B also integrates EEPROM used to configure the serial number, etc.
- ● SOP-16 and SSOP-20 and MSOP-10 lead-free RoHS compliant package.


###### 3 Package

![image 2](<ch340g_datasheet_images/imageFile2.png>)

|Package shape|Width of plastic| |Pitch of Pin| |Instruction of package|Ordering type|
|---|---|---|---|---|---|---|
|SOP-16|3.9mm|150mil|1.27mm|50mil|Small outline package of 16-pin|CH340G|
|SOP-16|3.9mm|150mil|1.27mm|50mil|Small outline package of 16-pin|CH340C|
|SOP-16|3.9mm|150mil|1.27mm|50mil|Small outline package of 16-pin|CH340B|
|MSOP-10|3.0mm|118mil|0.50mm|19.7mil|Shrink small outline package of 10-pin|CH340E|
|SSOP-20|5.30mm|209mil|0.65mm|25mil|Shrink small outline package of 20-pin|CH340T|
|SSOP-20|5.30mm|209mil|0.65mm|25mil|Shrink small outline package of 20-pin|CH340R|


Model differences: CH340C, CH340E and CH340B have built-in crystal, no external crystal; CH340B also has built-in EEPROM used to configure the serial number,etc.Some functions can be customized. CH340R provides reverse polarity TXD and MODEM signals. (No spot)

###### 4. Pins

|SSOP20 Pin No.|SOP16 Pin No.|MSOP10 Pin No.|Pin Name|Pin Type|Pin Description (description in bracket is only about CH340R)|
|---|---|---|---|---|---|
|19|16|7|VCC|POWER|Positive power input port, requires an external 0.1uF power decoupling capacitor|
|8|1|3|GND|POWER|Public ground, ground connection for USB|
|5|4|NONE|V3|POWER|Connect to VCC to input external power when 3.3V power supply, connect to 0.1uF decoupling capacitor when 5V power supply|
|9|7|NONE|XI|IN|CH340T/R/G: Input of crystal oscillator, connect to crystal and capacitor|
| | | |NC.|NONE|CH340C: No Connection, must be suspended|
| | | |RST#|IN|CH340B: Input of external reset, active low, built-in|


| | | | | |pull-up resistor|
|---|---|---|---|---|---|
|10|8<br><br>|NONE|XO|OUT|CH340T/R/G: Output of crystal oscillator, connect to crystal and capacitor|
| | | |NC.|NONE|CH340C/B: No Connection, must be suspended|
|6|5|1|UD+|USB signal|Directly connect to D+ data wire of USB bus|
|7|6|2|UD-|USB signal|Directly connect to D- data wire of USB bus|
|20|NONE|NONE|NOS#|IN|Forbid USB device suspending, active low, built-in pull-up resistor|
|3|2|8|TXD|OUT|Transmit asynchronous data output(reverse output for CH340R)|
|4|3|9|RXD|IN|Receive asynchronous data input, built-in configurable pull-up and pull-down resistor|
|11|9|5|CTS#|IN|MODEM liaison input signal, clear to send, active low(high)|
|12|10|NONE|DSR#|IN|MODEM liaison input signal, data set ready, active low(high)|
|13|11|NONE|RI#|IN|MODEM liaison input signal, ring indicator , active low(high)|
|14|12|NONE|DCD#|IN|MODEM liaison input signal, data carrier detect, active low(high)|
|15|13|NONE|DTR#|OUT|MODEM liaison output signal, data terminal ready, active low(high)|
|16|14|4|RTS#|OUT|MODEM liaison output signal, request to send, active low(high)|
|2|NONE|NONE|ACT#|OUT|USB configuration completed state output, active low|
|18|15|NONE|R232|IN|CH340T/R/G/C: Assistant RS232 enable, active high, built-in pull-down resistor|
|17|15<br><br>|6|TNOW|OUT|CH340T/E/B: Ongoing data transmission status indicator, active high|
| | | |IR#|IN|CH340R:Serial mode input setting, built-in pull-up resistor, SIR infrared serial interface when low, common serial interface when high|
|1<br><br>|NONE|NONE|CK0|OUT|CH340T: clock output|
| | | |NC.|NONE|CH340R:No Connection, must be suspend|


###### 5. Function Description

CH340 chip has built-in USB pull-up resistor, UD+ and UD- pins must be connected to USB bus directly. CH340 chip has built-in power-on reset circuit. CH340B also provides low active external reset pin. CH340G/CH340T/CH340R chips need to work with 12MHz clock signal supplied to XI pin. Generally,

clock signal is generated crystal oscillation with inverter in CH340. The peripheral circuit needs to place a crystal of 12MHz between XI and XO, and connect to a capacitor to ground separately.

CH340C, CH340E and CH340B chip have built-in clock generator, no external crystal and oscillating capacitor required.

CH340B chip also provides EEPROM for configuring data area, product serial number and other information could be customized for each chip by specific software tools, configurable data area is shown in the table below.

|Byte Address|Abbreviati on|Description of chip configuration data area|Default|
|---|---|---|---|
|00H|SIG|For CH340B: Internal configuration information valid reg, must be 58H. For CH340H/S: External configuration information valid reg, must be 53H. Invalid for other value|00H|
|01H|MODE|Serial mode, must be 23H|23H|
|02H|CFG|Specific configuration of chip,<br><br>bit5 is used to configure product Serial Number: 0= valid; 1= invalid.|FEH|
|03H|WP|Internal configuration information write protect flag，57H imply read only, otherwise can be rewrite|00H|
|05~04H|VID|Vendor ID, high byte is behind, any value. Set to 0000H or 0FFFFH implies VID and PID using vendor default value|1A86H|
|07~06H|PID|Product ID, high byte is behind, any value|7523H|
|0AH|PWR|Max Power, The maximum supply current in 2mA units|31H|
|17~10H|SN|Serial Number, the length of ASCII string is 8, disable the Serial number when the first byte is not ASCII character (21H~7FH)|12345678|
|3FH~1AH|PROD|For CH340B: Product String, Unicode string for Product description. The first byte is by total bytes (less than 26H), the next byte is 03H, Unicode string after that, using vendor default description when do not meet characteristics above.|Using product default description when the first byte is 00H|
|Others| |(Reserved unit)|00H or FFH|


CH340 chip supports 5V and 3.3V power voltage. When using 5V source power, the VCC pin input 5V power and the V3 pin should connect with decoupling 0.1uF capacitor. When using 3.3V power voltage, connects V3 with VCC, both input 3.3V power voltage, and the other circuit voltage which connected with CH340 cannot exceed 3.3V.

CH340 supports USB device suspending automatically to save power. USB device suspend is forbidden when NOS# is driven low.

The DTR# pin of CH340 is used as a configuration input pin before the USB configuration is complete. An external 4.7KΩ pull-down resistor can be connected with this pin to generate default low level during USB enumeration, apply larger supply current to the USB bus via the configuration descriptor.

In UART mode, CH340 chip contains these pins: data transfer pins, MODEM liaison signal pins and assistant pins.

Data transfer pins contain: TXD and RXD pin. RXD keeps high when UART reception is idle. For CH340G/C/T/R chip, If R232 pin is driven high, assistant RS232 function will be enabled, an internal inverter will automatically insert to the RXD pin , and the pin becomes low by default. When UART transmission is idle, the TXD pin of CH340G/C/B/T keeps high, while CH340R keeps low.

MODEM liaison signal pins contain: CTS#, DSR#, RI#, DCD# and RTS#. All these MODEM liaison signal are controlled and function defined by computer applications.

Assistant pins contain: IR#, R232, CK0, ACT# and TNOW. When IR# is low-level, infrared serial interface mode is enabled. R232 pin is used to control assistant RS232 function. If R232 pin is driven high, the RXD pin input will be reversed automatically. ACT# pin is USB device configuration complete status output (such as USB infrared adapter ready). TNOW pin indicates CH340 is transmitting data from UART when it is high-level and becomes low when transmit over. In RS485 and other half-duplex mode, TNOW could be used to indicate UART transmit-receive status. IR# and R232 are detected only once when chip powered on and reset.

CH340 has built-in separate transmit-receive buffer and supports simplex, half-duplex and full duplex UART communication. Serial data contains one low-level start bit , 5, 6, 7 or 8 data bits and 1 or 2 high-level stop bits, supports odd/even/mark/space check. CH340 supports common baud rate: 50, 75, 100, 110, 134.5, 150, 300, 600, 900, 1200, 1800, 2400, 3600, 4800, 9600, 14400, 19200, 33600, 38400, 56000, 57600, 76800, 115200, 128000, 153600, 230400, 460800, 921600, 1500000, 2000000 etc.

The baud rate error of CH340 UART reception allows not less than 2%, the baud rate error of CH340G/CH340T/CH340R UART transmission is less than 0.3%, less than 1% for CH340C/CH340E/ CH340B.

In the Windows operation system, CH340 driver can emulate standard serial port. So the mostly original serial applications are totally compatible, without any modification.

CH340 can be used to upgrade the serial interface peripherals, or expand extra serial port for computers via USB bus, through external level conversion chip provide further RS232, RS485, RS422 interface, etc.

Only need to add infrared transceiver, CH340R can expand SIR infrared adapter for computer via USB bus, realize infrared communication between computer and peripheral equipment that comply with IrDA specifications.

###### 6. Parameter

- 6.1 Absolute maximum rating (Critical or exceeding absolute maximum can cause permanent damage to device)


|Name|Parameter Description| |Min.|Max.|Units|
|---|---|---|---|---|---|
|TA|Ambient temperature|CH340G/CH340T/CH340R|-40|85|℃|
| | |CH340C/CH340E/CH340B|-20|70|℃|
|TS|Storage temperature| |-55|125|℃|
|VCC|Supply Voltage(VCC connects to power, GND to ground)| |-0.5|6.0|V|
|VIO|The voltage of input or output pin| |-0.5|VCC+0.5|V|


- 6.2. Electrical Parameter (test conditions: TA=25℃, VCC=5V, exclude pin connected to USB bus) (All the current parameters should multiply the coefficient of 40% when the power is 3.3V)

|Name|Parameter Description|Min.|Typical|Max.|Units|
|---|---|---|---|---|---|


|VCC|Supply Voltage|V3 doesn’t connect to VCC| |4.0|5|5.3|V<br><br>|
|---|---|---|---|---|---|---|---|
| | |V3 connect to VCC|CH340G/T/R|2.8|3.3|3.6| |
| | | |CH340C/E/B|3.0|3.3|3.6| |
|ICC|Operating Supply Current(Normal Operation)| |CH340G/C/E/T/R| |7|20|mA|
| | | |CH340B| |6|15| |
|ISLP|Operating Supply Current(USB Suspend)<br><br>| |VCC=5V| |0.1|0.2|mA|
| | | |VCC=3.3V| |0.09|0.15| |
|VIL|Low-level Input Voltage| | |-0.5| |0.7|V|
|VIH|High-level Input Voltage| | |2.0| |VCC+0.5|V|
|VOL|Low-level Output Voltage(4mA draw current)| | | | |0.5|V|
|VOH|High-level Output Voltage(3mA output current) (Output 100uA current during chip reset)| | |VCC-0.5| | |V|
|IUP|Input current input with built-in pull-up resistor| | |3|150|300|uA|
|IDN|Input current input with built-in pull-down resistor| | |-50|-150|-300|uA|
|VR|Restrict voltage when power-up reset| | |2.4|2.6|2.8|V|


- 6.3. Sequence Parameter (test conditions: TA=25℃,VCC=5V)


|Name|Parameter Description|Min.|Typical|Max.|Units|
|---|---|---|---|---|---|
|FCLK|Frequency of input clock in XI|11.98|12.00|12.02|MHz|
|TPR|Reset time of power-up|20|35|50|mS|


###### 7. Application

- 7.1.1 USB to RS232 Converter Configuration using CH340T


![image 3](<ch340g_datasheet_images/imageFile3.png>)

- 7.1.2 USB to RS232 Converter Configuration using CH340B


![image 4](<ch340g_datasheet_images/imageFile4.png>)

The image above use CH340T/CH340B (or CH340C ) to realize USB to RS232 converter. CH340 provides common UART and MODEM signal, converts TTL to RS232 through level conversion chip U8. Port P11 is DB9 connector, the pin and its function are the same as common PC DB9 connector, the chips similar with U8 have MAX213/ADM213/SP213/MAX211 etc.

U8 and C46/C47/C48/C49/C40 could be removed when realize USB to TTL converter only. The signal lines in the image only RXD、TXD and public ground need connected, the other signal lines should suspend when not use.

P2 is USB port, USB bus contains a pair of 5V power lines and a pair of data signal lines . Usually, the color of

+5V power line is red, the black one is ground. D+ signal line is green and the D- signal line is white. The max supply current of USB bus is up to 500mA. Generally, CH340 and low power consumption USB products can use the 5V power supplied by USB bus directly. If the USB products supply standing power by other manner, CH340 should use this power too. If the USB bus power and standing power are necessary at the same time, connect a 1Ω resistor between USB bus 5V power line and USB products 5V standing power line, and connect the ground lines of these two power directly.

The capacitor C8 on V3 pin is 0.1uF, used to CH340 internal power node decoupling. The capacitor C9 is 0.1uF, used to external power decoupling.

For CH340G/T/R chip, Crystal X2, capacitor C6 and C7 are used for clock oscillation circuit. The X2 is 12MHz quartz crystal, C6 and C7 are monolithic or high frequency ceramic capacitors with 22pF. If X2 is ceramic with low cost, C6 and C7 must use the recommended value of crystal manufacturer and generally is 47pF. For the crystal which is difficult to oscillate, halved value is suggested for C6.

For CH340C/E/B chip, crystal X2 and capacitor C6, C7 are not required.

When designing the PCB, pay attention to: decoupling capacitor C8 and C9 must keep near to connection pin of CH340; makes sure D+ and D- signal lines are parallel and supply ground lines or pour copper beside them to decrease the interference from outside signal; the signal lines relevant to XI and XO should be kept as short as possible. In order to lessen the high frequency interference, around the ground wire or pour copper around the relevant components.

- 7.2. USB to RS232 Converter Configuration (3-wire) The image below is USB to 3-wire RS232 converter design which is the most basic and most commonly used,


U5 uses MAX232/ICL232/SP232 etc.

![image 5](<ch340g_datasheet_images/imageFile5.png>)

- 7.3 USB to RS232 Converter Configuration (Simplified version using R232)

![image 6](<ch340g_datasheet_images/imageFile6.png>)

The image above is USB to RS232 converter design too, the function of this circuit is the same with 7.2 section except the range of output RS232 is narrower. When R232 pin is driven high, the assistant RS232 function will be enabled, just need to add some diodes, transistors, resistors and capacitors, the special level conversion chip U5 in section 7.2 could be replaced and the hardware cost is lower.

- 7.4 USB to Infrared Adapter

![image 7](<ch340g_datasheet_images/imageFile7.png>)

The image above is USB to infrared adapter design is composed with USB convert IrDA infrared chip CH340R and infrared transceiver U14 (ZHX1810/HSDL3000 etc).The resistor R13 is used to weaken influence of large current in infrared transmitting. The current limiting resistor R14 should be adjusted according to the manufacturer’s recommended value of the infrared transceiver U14.

- 7.5. USB to RS485 Converter Configuration


The TNOW pin can be used to control DE (high active send enable) and RE# (low active receive enable) pin of RS485 transceiver.

