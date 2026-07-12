|Name|Parameter Description|Min.|Typical|Max.|Units|
|---|---|---|---|---|---|

|VCC|Supply Voltage|V3 doesn’t connect to VCC|Col4|4.0|5|5.3|V|
|---|---|---|---|---|---|---|---|
|VCC|Supply Voltage|V3 connect to<br>VCC|CH340G/T/R|2.8|3.3|3.6|3.6|
|VCC|Supply Voltage|V3 connect to<br>VCC|CH340C/E/B|3.0|3.3|3.6|3.6|
|ICC|Operating Supply<br>Current(Normal Operation)|Operating Supply<br>Current(Normal Operation)|CH340G/C/E/T/R||7|20|mA|
|ICC|Operating Supply<br>Current(Normal Operation)|Operating Supply<br>Current(Normal Operation)|CH340B||6|15|15|
|ISLP|Operating Supply Current(USB<br>Suspend)|Operating Supply Current(USB<br>Suspend)|VCC=5V||0.1|0.2|mA|
|ISLP|Operating Supply Current(USB<br>Suspend)|Operating Supply Current(USB<br>Suspend)|VCC=3.3V||0.09|0.15|0.15|
|VIL|Low-level Input Voltage|Low-level Input Voltage|Low-level Input Voltage|-0.5||0.7|V|
|VIH|High-level Input Voltage|High-level Input Voltage|High-level Input Voltage|2.0||VCC+0.5|V|
|VOL|Low-level Output Voltage(4mA draw current)|Low-level Output Voltage(4mA draw current)|Low-level Output Voltage(4mA draw current)|||0.5|V|
|VOH|High-level Output Voltage(3mA output current)<br>(Output 100uA current during chip reset)|High-level Output Voltage(3mA output current)<br>(Output 100uA current during chip reset)|High-level Output Voltage(3mA output current)<br>(Output 100uA current during chip reset)|VCC-0.5|||V|
|IUP|Input current input with built-in pull-up resistor|Input current input with built-in pull-up resistor|Input current input with built-in pull-up resistor|3|150|300|uA|
|IDN|Input current input with built-in pull-down resistor|Input current input with built-in pull-down resistor|Input current input with built-in pull-down resistor|-50|-150|-300|uA|
|VR|Restrict voltage when power-up reset|Restrict voltage when power-up reset|Restrict voltage when power-up reset|2.4|2.6|2.8|V|

|Name|Parameter Description|Min.|Typical|Max.|Units|
|---|---|---|---|---|---|
|FCLK|Frequency of input clock in XI|11.98|12.00|12.02|MHz|
|TPR|Reset time of power-up|20|35|50|mS|