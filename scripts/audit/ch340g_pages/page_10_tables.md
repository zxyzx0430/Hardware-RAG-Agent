|Byte<br>Address|Abbreviati<br>on|Description of chip configuration data area|Default|
|---|---|---|---|
|00H|SIG|For CH340B: Internal configuration information valid reg,<br>must be 58H.<br>For CH340H/S: External configuration information valid<br>reg, must be 53H.<br>Invalid for other value|00H|
|01H|MODE|Serial mode, must be 23H|23H|
|02H|CFG|Specific configuration of chip,<br>bit5 is used to configure product Serial Number:<br>0= valid; 1= invalid.|FEH|
|03H|WP|Internal configuration information write protect flag，57H<br>imply read only, otherwise can be rewrite|00H|
|05~04H|VID|Vendor ID, high byte is behind, any value. Set to 0000H or<br>0FFFFH implies VID and PID using vendor default value|1A86H|
|07~06H|PID|Product ID, high byte is behind, any value|7523H|
|0AH|PWR|Max Power, The maximum supply current in 2mA units|31H|
|17~10H|SN|Serial Number, the length of ASCII string is 8, disable the<br>Serial number when the first byte is not ASCII character<br>(21H~7FH)|12345678|
|3FH~1AH|PROD|For CH340B: Product String, Unicode string for Product<br>description. The first byte is by total bytes (less than 26H),<br>the next byte is 03H, Unicode string after that, using vendor<br>default description when do not meet characteristics above.|Using product<br>default<br>description when<br>the first byte is<br>00H|
|Others||(Reserved unit)|00H or FFH|