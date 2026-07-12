|Package shape|Width of plastic|Col3|Pitch of Pin|Col5|Instruction of package|Ordering type|
|---|---|---|---|---|---|---|
|SOP-16|3.9mm|150mil|1.27mm|50mil|Small outline package of 16-pin|CH340G|
|SOP-16|3.9mm|150mil|1.27mm|50mil|Small outline package of 16-pin|CH340C|
|SOP-16|3.9mm|150mil|1.27mm|50mil|Small outline package of 16-pin|CH340B|
|MSOP-10|3.0mm|118mil|0.50mm|19.7mil|Shrink small outline package of 10-pin|CH340E|
|SSOP-20|5.30mm|209mil|0.65mm|25mil|Shrink small outline package of 20-pin|CH340T|
|SSOP-20|5.30mm|209mil|0.65mm|25mil|Shrink small outline package of 20-pin|CH340R|

|SSOP20<br>Pin No.|SOP16<br>Pin No.|MSOP10<br>Pin No.|Pin Name|Pin Type|Pin Description (description in bracket is only about<br>CH340R)|
|---|---|---|---|---|---|
|19|16|7|VCC|POWER|Positive power input port, requires an external 0.1uF power<br>decoupling capacitor|
|8|1|3|GND|POWER|Publicground,ground connection for USB|
|5|4|NONE|V3|POWER|Connect to VCC to input external power when 3.3V power<br>supply, connect to 0.1uF decoupling capacitor when 5V<br>power supply|
|9|7|NONE|XI|IN|CH340T/R/G: Input of crystal oscillator, connect to crystal<br>and capacitor|
|9|7|NONE|NC.|NONE|CH340C: No Connection, must be suspended|
|9|7|NONE|RST#|IN|CH340B: Input of external reset, active low, built-in|