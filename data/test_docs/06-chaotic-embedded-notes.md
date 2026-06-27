这是一份没有任何结构化标记的嵌入式调试笔记，所有内容都是流水账式记录，用于压测分块策略。GPIO 调试是嵌入式开发里最基础也最容易翻车的部分，STM32 的 GPIO 模式配置在 Reference Manual 的 GPIO section 里有详细描述，但是新手经常把 GPIO_Mode_IPU 和 GPIO_Mode_IPD 搞反，上拉是 IPU 下拉是 IPD，记住 U 是 Up D 是 Down 就不会错。下面这段代码是把 STM32F103 的 PA0 配成输入上拉，PA5 配成推挽输出 50MHz 驱动 LED，注意时钟必须先开否则寄存器写不进去。
```c
// stm32f1xx_ll_gpio.c style
#include "stm32f1xx_ll_bus.h"
#include "stm32f1xx_ll_gpio.h"

void gpio_init_led_button(void) {
    // enable clock for GPIOA, critical step, forget this and regs stay 0
    LL_APB2_GRP1_EnableClock(LL_APB2_GRP1_PERIPH_GPIOA);

    // PA0 input pull-up, used as user button on Discovery board
    LL_GPIO_SetPinMode(GPIOA, LL_GPIO_PIN_0, LL_GPIO_MODE_INPUT);
    LL_GPIO_SetPinPull(GPIOA, LL_GPIO_PIN_0, LL_GPIO_PULL_UP);

    // PA5 push-pull output 50MHz, onboard green LED LD2
    LL_GPIO_SetPinMode(GPIOA, LL_GPIO_PIN_5, LL_GPIO_MODE_ALTERNATE);
    LL_GPIO_SetPinMode(GPIOA, LL_GPIO_PIN_5, LL_GPIO_MODE_OUTPUT);
    LL_GPIO_SetPinSpeed(GPIOA, LL_GPIO_PIN_5, LL_GPIO_SPEED_FREQ_HIGH);
    LL_GPIO_SetPinOutputType(GPIOA, LL_GPIO_PIN_5, LL_GPIO_OUTPUT_PUSHPULL);
    LL_GPIO_SetPinPull(GPIOA, LL_GPIO_PIN_5, LL_GPIO_PULL_NO);

    // initial state: LED off (active high on F103 Discovery)
    LL_GPIO_ResetOutputPin(GPIOA, LL_GPIO_PIN_5);

    // read button: 0 means pressed because pull-up + button to GND
    uint8_t pressed = (LL_GPIO_ReadInputPort(GPIOA) & LL_GPIO_PIN_0) ? 0 : 1;
    (void)pressed;
}
```
上面代码里有个坑，F1 系列的输出模式要先设 MODE 位再设 CNF 位，顺序反了有时候会瞬间短通，F4/F7/H7 系列就没有这个问题，因为它们用的是 MODER 寄存器单独管模式，OTYPER 单独管输出类型，两个寄存器独立。GPIO 模式速查表（注意这是 tab 分隔的没有表头分隔符）：
GPIOA|MODER bits|用途|上下拉
PA0|00 input|按键输入|上拉
PA5|01 output|LED 驱动|无
PA9|10 AF|USART1_TX|无
PA10|10 AF|USART1_RX|无
PA11|10 AF|USB_DM|无
PA12|10 AF|USB_DP|无
PB6|10 AF|I2C1_SCL|上拉
PB7|10 AF|I2C1_SDA|上拉
PA5|01 output|SPI1_SSEL 软片选|无

ESP32 的 GPIO 和 STM32 完全不是一个套路，ESP32 用的是 gpio_config_t 结构体一次配一堆 pin，而且 ESP32 的 GPIO 有 RTC GPIO 和 Digital GPIO 之分，输入输出还要看 RTC_MUX 寄存器决定走哪个域。ESP32 的引脚有几个不能用的：GPIO6-11 被 flash 占用，GPIO34-39 只能输入不能输出（没有输出使能），GPIO0 是 strapping pin 上电要为高否则进 download mode。下面这段 ESP32 代码把 GPIO18 配成输出驱动 LED，GPIO0 配成输入做按键，注意 ESP-IDF 里用 gpio_set_level 来输出电平。```c
// esp32_gpio_example.c
#include "driver/gpio.h"
#include "esp_log.h"

static const char *TAG = "gpio_demo";

void esp32_gpio_init(void) {
    // LED on GPIO18, active high
    gpio_config_t io_conf = {};
    io_conf.intr_type = GPIO_INTR_DISABLE;
    io_conf.mode = GPIO_MODE_OUTPUT;
    io_conf.pin_bit_mask = (1ULL << GPIO_NUM_18);
    io_conf.pull_down_en = 0;
    io_conf.pull_up_en = 0;
    gpio_config(&io_conf);

    // button on GPIO0 with pull-up, falling edge interrupt
    io_conf.intr_type = GPIO_INTR_NEGEDGE;
    io_conf.mode = GPIO_MODE_INPUT;
    io_conf.pin_bit_mask = (1ULL << GPIO_NUM_0);
    io_conf.pull_up_en = 1;
    gpio_config(&io_conf);

    // install ISR service
    gpio_install_isr_service(ESP_INTR_FLAG_DEFAULT);
    gpio_isr_handler_add(GPIO_NUM_0, button_isr_handler, NULL);

    ESP_LOGI(TAG, "gpio init done");
    gpio_set_level(GPIO_NUM_18, 1); // turn LED on
}
```
GPIO 内部上拉电阻阻值问题，STM32 内部上拉典型值 40kΩ 弱上拉，ESP32 内部上拉典型值 45kΩ 也是弱上拉，做 I2C 这种要 4.7kΩ 外部上拉，内部上拉根本不够用，I2C 总线只要挂的设备多了或者线长了波形就烂掉，示波器上看到 SCL 上升沿变圆就是上拉不够。SPI 是全双工同步串行总线，比 I2C 快很多，SPI 没有 I2C 那种地址机制所以要用片选 CS 来选设备，一个 CS 对应一个从设备。SPI 调试最常见的问题是时钟极性 CPOL 和时钟相位 CPHA 配错，主从必须一致否则数据移位错位。CPOL=0 表示空闲低电平，CPOL=1 表示空闲高电平，CPHA=0 表示第一个边沿采样，CPHA=1 表示第二个边沿采样。Mode 0 是 CPOL=0 CPHA=0 最常用，Mode 3 是 CPOL=1 CPHA=1 也常见，W25Q64 flash 默认 Mode 0 和 Mode 3 都支持。下面这段 STM32 SPI1 主机初始化代码驱动 W25Q64 flash，时钟分频到 36MHz（APB2=72MHz /2）：```c
// stm32_spi1_master.c
#include "stm32f1xx_ll_spi.h"
#include "stm32f1xx_ll_bus.h"
#include "stm32f1xx_ll_gpio.h"

void spi1_init_master(void) {
    // clocks: SPI1 on APB2, GPIOA on APB2, AFIO on APB2
    LL_APB2_GRP1_EnableClock(LL_APB2_GRP1_PERIPH_SPI1);
    LL_APB2_GRP1_EnableClock(LL_APB2_GRP1_PERIPH_GPIOA);
    LL_APB2_GRP1_EnableClock(LL_APB2_GRP1_PERIPH_AFIO);

    // PA5=SCK, PA7=MOSI as AF push-pull 50MHz
    LL_GPIO_SetPinMode(GPIOA, LL_GPIO_PIN_5, LL_GPIO_MODE_ALTERNATE);
    LL_GPIO_SetPinMode(GPIOA, LL_GPIO_PIN_7, LL_GPIO_MODE_ALTERNATE);
    LL_GPIO_SetPinSpeed(GPIOA, LL_GPIO_PIN_5, LL_GPIO_SPEED_FREQ_HIGH);
    LL_GPIO_SetPinSpeed(GPIOA, LL_GPIO_PIN_7, LL_GPIO_SPEED_FREQ_HIGH);
    LL_GPIO_SetPinOutputType(GPIOA, LL_GPIO_PIN_5, LL_GPIO_OUTPUT_PUSHPULL);
    LL_GPIO_SetPinOutputType(GPIOA, LL_GPIO_PIN_7, LL_GPIO_OUTPUT_PUSHPULL);

    // PA6=MISO as input floating
    LL_GPIO_SetPinMode(GPIOA, LL_GPIO_PIN_6, LL_GPIO_MODE_INPUT);
    LL_GPIO_SetPinPull(GPIOA, LL_GPIO_PIN_6, LL_GPIO_PULL_NO);

    // PA4=CS manual control as push-pull output
    LL_GPIO_SetPinMode(GPIOA, LL_GPIO_PIN_4, LL_GPIO_MODE_OUTPUT);
    LL_GPIO_SetPinSpeed(GPIOA, LL_GPIO_PIN_4, LL_GPIO_SPEED_FREQ_HIGH);
    LL_GPIO_SetPinOutputType(GPIOA, LL_GPIO_PIN_4, LL_GPIO_OUTPUT_PUSHPULL);
    LL_GPIO_SetOutputPin(GPIOA, LL_GPIO_PIN_4); // CS high = idle

    // SPI config: master, 8-bit, Mode 0, MSB first, baudrate PCLK/2 = 36MHz
    LL_SPI_SetBaudRatePrescaler(SPI1, LL_SPI_BAUDRATEPRESCALER_DIV2);
    LL_SPI_SetTransferDirection(SPI1, LL_SPI_FULL_DUPLEX);
    LL_SPI_SetClockPolarity(SPI1, LL_SPI_POLARITY_LOW);
    LL_SPI_SetClockPhase(SPI1, LL_SPI_PHASE_1EDGE);
    LL_SPI_SetBitOrder(SPI1, LL_SPI_MSB_FIRST);
    LL_SPI_SetTransferBitOrder(SPI1, LL_SPI_MSB_FIRST);
    LL_SPI_SetMode(SPI1, LL_SPI_MODE_MASTER);
    LL_SPI_SetDataWidth(SPI1, LL_SPI_DATAWIDTH_8BIT);
    LL_SPI_SetNSSMode(SPI1, LL_SPI_NSS_SOFT);
    LL_SPI_Enable(SPI1);
}

uint8_t spi1_transfer(uint8_t tx) {
    // wait until TXE set, write data, wait until RXNE set, read data
    while (!LL_SPI_IsActiveFlag_TXE(SPI1)) {}
    LL_SPI_TransmitData8(SPI1, tx);
    while (!LL_SPI_IsActiveFlag_RXNE(SPI1)) {}
    return LL_SPI_ReceiveData8(SPI1);
}

void w25q_read_id(uint16_t *id) {
    LL_GPIO_ResetOutputPin(GPIOA, LL_GPIO_PIN_4); // CS low
    spi1_transfer(0x90); // JEDEC ID command
    uint8_t mfr = spi1_transfer(0x00);
    uint8_t dev = spi1_transfer(0x00);
    LL_GPIO_SetOutputPin(GPIOA, LL_GPIO_PIN_4); // CS high
    *id = ((uint16_t)mfr << 8) | dev; // expect 0xEF16 for W25Q64
}
```
SPI 时钟模式速查表（tab 分隔）：
Mode	CPOL	CPHA	空闲电平	采样沿	典型设备
0	0	0	低	第一个（上升）	W25Q64 flash 默认
1	0	1	低	第二个（下降）	某些 LCD
2	1	0	高	第一个（下降）	少见
3	1	1	高	第二个（上升）	W25Q64 也支持 SD card

SPI 调试技巧：用逻辑分析仪抓 SCLK/MOSI/MISO/CS 四根线，先确认时钟频率对不对，再确认 CS 拉低时序，再对 MOSI 数据逐字节比对，W25Q64 发 0x90 读 ID 返回 0xEF 0x16，如果返回 0xFF 0xFF 说明 CS 没拉低或者设备没上电，如果返回 0x00 0x00 说明 MISO 没接好或者时钟太快要降频。SPI 波特率计算：STM32F1 SPI1 在 APB2=72MHz 上，分频因子只能是 2/4/8/16/32/64/128/256，DIV2=36MHz，DIV4=18MHz，DIV8=9MHz。ESP32 的 SPI 用 spi_bus_initialize 和 spi_bus_add_device 两步走，最大时钟 80MHz 但实际 PCB 走线长的话 40MHz 就开始出问题。ESP32 SPI 主机配置示例：
```c
// esp32_spi_master.c
#include "driver/spi_master.h"
#include "esp_log.h"

static const char *TAG = "spi_demo";

spi_device_handle_t spi;

void esp32_spi_init(void) {
    spi_bus_config_t buscfg = {
        .miso_io_num = 19,
        .mosi_io_num = 23,
        .sclk_io_num = 18,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = 4096,
    };
    spi_device_interface_config_t devcfg = {
        .clock_speed_hz = 10 * 1000 * 1000, // 10 MHz
        .mode = 0,                           // CPOL=0, CPHA=0
        .spics_io_num = 5,
        .queue_size = 7,
        .flags = SPI_DEVICE_HALFDUPLEX,
    };
    esp_err_t ret = spi_bus_initialize(SPI2_HOST, &buscfg, SPI_DMA_CH_AUTO);
    ESP_ERROR_CHECK(ret);
    ret = spi_bus_add_device(SPI2_HOST, &devcfg, &spi);
    ESP_ERROR_CHECK(ret);
    ESP_LOGI(TAG, "spi2 init ok");
}

uint16_t esp32_w25q_read_id(void) {
    uint8_t tx[4] = {0x90, 0x00, 0x00, 0x00};
    uint8_t rx[4] = {0};
    spi_transaction_t t = {};
    t.length = 4 * 8;
    t.tx_buffer = tx;
    t.rx_buffer = rx;
    spi_device_polling_transmit(spi, &t);
    return ((uint16_t)rx[2] << 8) | rx[3]; // 0xEF16 expected
}
```
I2C 调试比 SPI 麻烦因为 I2C 是半双工开漏输出，需要上拉电阻，而且 I2C 有地址冲突、时钟拉伸、NACK 这些复杂状态。I2C 总线两根线 SCL 和 SDA 都是开漏，必须接上拉电阻到 VCC，典型值 4.7kΩ，速率越高上拉要越小，400kHz fast mode 一般用 4.7kΩ，1MHz fast mode plus 要降到 2.2kΩ。I2C 7 位地址范围 0x08-0x77，0x00-0x07 和 0x78-0x7F 是保留地址不能用。MPU6050 的地址是 0x68（AD0 接 GND）或 0x69（AD0 接 VCC），AT24C02 EEPROM 地址是 0x50-0x57 低位由 A0/A1/A2 决定。STM32 I2C 调试最坑的是 F1/F4 的 I2C IP 设计有 bug，经常出现 BUSY 标志位卡死，官方 errata 说要先配成软件复位再把 GPIO 切回 AF 才能恢复，下面这段代码是 STM32F4 I2C1 主机读 MPU6050 WHO_AM_I 寄存器，预期返回 0x68：
```c
// stm32_i2c1_mpu6050.c
#include "stm32f4xx_ll_i2c.h"
#include "stm32f4xx_ll_bus.h"
#include "stm32f4xx_ll_gpio.h"
#include "stm32f4xx_ll_utils.h"

#define MPU6050_ADDR        0x68
#define MPU6050_WHO_AM_I    0x75

void i2c1_init(void) {
    LL_APB1_GRP1_EnableClock(LL_APB1_GRP1_PERIPH_I2C1);
    LL_AHB1_GRP1_EnableClock(LL_AHB1_GRP1_PERIPH_GPIOB);

    // PB6=SCL, PB7=SDA, AF4 open-drain, pull-up
    LL_GPIO_SetPinMode(GPIOB, LL_GPIO_PIN_6, LL_GPIO_MODE_ALTERNATE);
    LL_GPIO_SetPinMode(GPIOB, LL_GPIO_PIN_7, LL_GPIO_MODE_ALTERNATE);
    LL_GPIO_SetPinAF(GPIOB, LL_GPIO_PIN_6, LL_GPIO_AF_4);
    LL_GPIO_SetPinAF(GPIOB, LL_GPIO_PIN_7, LL_GPIO_AF_4);
    LL_GPIO_SetPinOutputType(GPIOB, LL_GPIO_PIN_6, LL_GPIO_OUTPUT_OPENDRAIN);
    LL_GPIO_SetPinOutputType(GPIOB, LL_GPIO_PIN_7, LL_GPIO_OUTPUT_OPENDRAIN);
    LL_GPIO_SetPinPull(GPIOB, LL_GPIO_PIN_6, LL_GPIO_PULL_UP);
    LL_GPIO_SetPinPull(GPIOB, LL_GPIO_PIN_7, LL_GPIO_PULL_UP);
    LL_GPIO_SetPinSpeed(GPIOB, LL_GPIO_PIN_6, LL_GPIO_SPEED_FREQ_HIGH);
    LL_GPIO_SetPinSpeed(GPIOB, LL_GPIO_PIN_7, LL_GPIO_SPEED_FREQ_HIGH);

    // I2C config: 100kHz standard mode, APB1=42MHz on F407
    LL_I2C_SetMode(I2C1, LL_I2C_MODE_I2C);
    LL_I2C_SetClockSpeed(I2C1, LL_I2C_ANALOGFILTER_ENABLE, 100000);
    LL_I2C_Enable(I2C1);
}

uint8_t i2c1_read_reg(uint8_t dev_addr, uint8_t reg) {
    // 7-bit addr must be shifted left by 1 for STM32 I2C DR
    uint8_t addr_w = (dev_addr << 1);
    uint8_t addr_r = (dev_addr << 1) | 1;

    LL_I2C_HandleTransfer(I2C1, addr_w, LL_I2C_ADDRSLAVE_7BIT,
                          1, LL_I2C_MODE_SOFTEND, LL_I2C_GENERATE_START_WRITE);
    while (!LL_I2C_IsActiveFlag_TXIS(I2C1)) {}
    LL_I2C_TransmitData8(I2C1, reg);
    while (!LL_I2C_IsActiveFlag_TC(I2C1)) {}

    LL_I2C_HandleTransfer(I2C1, addr_r, LL_I2C_ADDRSLAVE_7BIT,
                          1, LL_I2C_MODE_AUTOEND, LL_I2C_GENERATE_START_READ);
    while (!LL_I2C_IsActiveFlag_RXNE(I2C1)) {}
    uint8_t val = LL_I2C_ReceiveData8(I2C1);
    while (!LL_I2C_IsActiveFlag_STOP(I2C1)) {}
    LL_I2C_ClearFlag_STOP(I2C1);
    return val;
}

uint8_t mpu6050_who_am_i(void) {
    return i2c1_read_reg(MPU6050_ADDR, MPU6050_WHO_AM_I); // expect 0x68
}
```
I2C 常见设备地址速查（pipe 分隔无表头分隔符）：
设备|7位地址|备注
MPU6050|0x68 / 0x69|AD0 决定
AT24C02|0x50-0x57|A0/A1/A2 决定
BMP280|0x76 / 0x77|SDO 决定
SSD1306 OLED|0x3C / 0x3D|D/C 决定
PCF8574 IO 扩展|0x20-0x27|A0/A1/A2 决定
DS3231 RTC|0x68|和 MPU6050 冲突不能同总线
HMC5883L|0x1E|磁力计
BME280|0x76 / 0x77|和 BMP280 兼容

I2C 调试技巧：示波器抓 SCL/SDA，看 START 条件是不是 SDA 在 SCL 高电平时下降，STOP 条件是不是 SDA 在 SCL 高电平时上升，每 9 个 SCL 周期从设备拉低 SDA 表示 ACK，如果第 9 个周期 SDA 是高就是 NACK，NACK 说明地址错或者设备没上电。时钟拉伸 clock stretching 是从设备在 ACK 后把 SCL 拉低让主机等，STM32 默认支持但有些主机实现不支持会出错。UART 调试是最简单的串口调试，但是波特率不匹配、电平不匹配、流控没接都会出问题。UART 是异步串行总线没有时钟线，主从双方必须用相同波特率，误差不能超过 2-3% 否则接收会错位。常用波特率 9600/19200/38400/57600/115200/230400/460800/921600。波特率计算公式：波特率 = fPCLK / (16 * USARTDIV)，USARTDIV 写到 BRR 寄存器。STM32F1 USART1 挂在 APB2=72MHz，115200 波特率时 BRR = 72000000 / (115200 * 16) = 39.0625，取整 0x271。下面这段 STM32 USART1 收发代码用中断方式接收，每收到一个字节回显，波特率 115200：
```c
// stm32_usart1_irq_echo.c
#include "stm32f1xx_ll_usart.h"
#include "stm32f1xx_ll_bus.h"
#include "stm32f1xx_ll_gpio.h"

#define USART1_BAUDRATE   115200

volatile uint8_t rx_byte;

void usart1_init(void) {
    LL_APB2_GRP1_EnableClock(LL_APB2_GRP1_PERIPH_USART1);
    LL_APB2_GRP1_EnableClock(LL_APB2_GRP1_PERIPH_GPIOA);
    LL_APB2_GRP1_EnableClock(LL_APB2_GRP1_PERIPH_AFIO);

    // PA9=TX AF push-pull 50MHz, PA10=RX input floating
    LL_GPIO_SetPinMode(GPIOA, LL_GPIO_PIN_9, LL_GPIO_MODE_ALTERNATE);
    LL_GPIO_SetPinOutputType(GPIOA, LL_GPIO_PIN_9, LL_GPIO_OUTPUT_PUSHPULL);
    LL_GPIO_SetPinSpeed(GPIOA, LL_GPIO_PIN_9, LL_GPIO_SPEED_FREQ_HIGH);
    LL_GPIO_SetPinMode(GPIOA, LL_GPIO_PIN_10, LL_GPIO_MODE_INPUT);
    LL_GPIO_SetPinPull(GPIOA, LL_GPIO_PIN_10, LL_GPIO_PULL_NO);

    LL_USART_SetBaudRate(USART1, SystemCoreClock, LL_USART_OVERSAMPLING_16, USART1_BAUDRATE);
    LL_USART_SetDataWidth(USART1, LL_USART_DATAWIDTH_8B);
    LL_USART_SetStopBitsLength(USART1, LL_USART_STOPBITS_1);
    LL_USART_SetParity(USART1, LL_USART_PARITY_NONE);
    LL_USART_SetTransferDirection(USART1, LL_USART_DIRECTION_TX_RX);
    LL_USART_SetHWFlowCtrl(USART1, LL_USART_HWCONTROL_NONE);

    LL_USART_EnableIT_RXNE(USART1);
    NVIC_SetPriority(USART1_IRQn, 5);
    NVIC_EnableIRQ(USART1_IRQn);

    LL_USART_Enable(USART1);
}

void usart1_send_byte(uint8_t b) {
    while (!LL_USART_IsActiveFlag_TXE(USART1)) {}
    LL_USART_TransmitData8(USART1, b);
    while (!LL_USART_IsActiveFlag_TC(USART1)) {}
}

void USART1_IRQHandler(void) {
    if (LL_USART_IsActiveFlag_RXNE(USART1)) {
        rx_byte = LL_USART_ReceiveData8(USART1);
        usart1_send_byte(rx_byte); // echo
    }
    if (LL_USART_IsActiveFlag_ORE(USART1)) {
        // overrun error: clear by reading SR then DR
        (void)LL_USART_ReadReg(USART1, SR);
        (void)LL_USART_ReadReg(USART1, DR);
    }
}
```
UART 波特率误差速查表（tab 分隔，假设 72MHz APB2）：
波特率	理论 BRR	实际 BRR	误差
9600	468.75	469	0.05%
19200	234.375	234	-0.16%
38400	117.1875	117	-0.16%
57600	78.125	78	0.16%
115200	39.0625	39	-0.16%
230400	19.53125	20	2.34% 临界
460800	9.765625	10	2.4% 临界会错位
921600	4.8828125	5	2.34% 临界

UART 调试技巧：示波器抓 TX 线，一个 bit 时间 = 1/波特率，115200 一个 bit 约 8.68us，起始位是低电平，数据位 LSB 先发，停止位高电平。如果收到的全是 0x00 或 0xFF 一般是波特率差太多或者 TX/RX 接反，如果收到的是乱码但不是全 0 一般是波特率误差在 5% 左右。电平问题：STM32 是 3.3V TTL 电平，不能直接接 PC 串口的 RS232 ±12V 电平，必须经过 MAX3232 这种电平转换芯片，ESP32 也是 3.3V TTL，和 PC 通信要用 CH340/CP2102 这种 USB 转串口芯片。UART framing error 表示停止位没收到高电平，一般是波特率不对或者线接错，overrun error ORE 表示上一个字节还没读走新字节又来了，要么提高中断优先级要么用 DMA。DMA 是 Direct Memory Access 直接内存访问，外设和内存之间搬数据不经过 CPU，能大幅降低 CPU 负载。STM32 DMA 调试最容易出错的是通道映射，F1 系列 DMA1 的每个通道固定映射到特定外设，比如 DMA1_Channel5 固定连到 USART1_RX，不能随便选，F4/F7/H7 用 DMA stream + channel 组合灵活一些但也要查表。DMA 传输方向有三种：外设到内存（接收）、内存到外设（发送）、内存到内存（ memcpy）。DMA 模式有 normal（传一次就停）和 circular（循环传，适合连续数据流）。下面这段 STM32F1 DMA1_Channel5 配合 USART1_RX 做循环接收的代码，把收到的数据放到 64 字节环形缓冲：
```c
// stm32_dma_usart1_rx.c
#include "stm32f1xx_ll_dma.h"
#include "stm32f1xx_ll_usart.h"
#include "stm32f1xx_ll_bus.h"

#define RX_BUF_SIZE 64

static uint8_t rx_buf[RX_BUF_SIZE];
volatile uint16_t dma_head = 0;
volatile uint16_t dma_tail = 0;

void dma_usart1_rx_init(void) {
    // DMA1 clock
    LL_AHB1_GRP1_EnableClock(LL_AHB1_GRP1_PERIPH_DMA1);

    // DMA1 Channel5 = USART1_RX on F1 (fixed mapping)
    LL_DMA_SetDataTransferDirection(DMA1, LL_DMA_CHANNEL_5,
                                    LL_DMA_DIRECTION_PERIPH_TO_MEMORY);
    LL_DMA_SetChannelPriorityLevel(DMA1, LL_DMA_CHANNEL_5, LL_DMA_PRIORITY_HIGH);
    LL_DMA_SetMode(DMA1, LL_DMA_CHANNEL_5, LL_DMA_MODE_CIRCULAR);
    LL_DMA_SetPeriphIncMode(DMA1, LL_DMA_CHANNEL_5, LL_DMA_PERIPH_NOINCREMENT);
    LL_DMA_SetMemoryIncMode(DMA1, LL_DMA_CHANNEL_5, LL_DMA_MEMORY_INCREMENT);
    LL_DMA_SetPeriphSize(DMA1, LL_DMA_CHANNEL_5, LL_DMA_PDATAALIGN_BYTE);
    LL_DMA_SetMemorySize(DMA1, LL_DMA_CHANNEL_5, LL_DMA_MDATAALIGN_BYTE);

    LL_DMA_SetPeriphAddress(DMA1, LL_DMA_CHANNEL_5, (uint32_t)&USART1->DR);
    LL_DMA_SetMemoryAddress(DMA1, LL_DMA_CHANNEL_5, (uint32_t)rx_buf);
    LL_DMA_SetDataLength(DMA1, LL_DMA_CHANNEL_5, RX_BUF_SIZE);

    // enable half-transfer and transfer-complete interrupts
    LL_DMA_EnableIT_TC(DMA1, LL_DMA_CHANNEL_5);
    LL_DMA_EnableIT_HT(DMA1, LL_DMA_CHANNEL_5);
    NVIC_SetPriority(DMA1_Channel5_IRQn, 4);
    NVIC_EnableIRQ(DMA1_Channel5_IRQn);

    // hand USART1 RX over to DMA
    LL_USART_EnableDMAReq_RX(USART1);
    LL_DMA_EnableChannel(DMA1, LL_DMA_CHANNEL_5);
}

uint16_t dma_bytes_available(void) {
    uint16_t head = RX_BUF_SIZE - LL_DMA_GetDataLength(DMA1, LL_DMA_CHANNEL_5);
    return (head >= dma_tail) ? (head - dma_tail) : (RX_BUF_SIZE + head - dma_tail);
}

uint8_t dma_read_byte(void) {
    uint8_t b = rx_buf[dma_tail];
    dma_tail = (dma_tail + 1) % RX_BUF_SIZE;
    return b;
}

void DMA1_Channel5_IRQHandler(void) {
    if (LL_DMA_IsActiveFlag_TC5(DMA1)) {
        LL_DMA_ClearFlag_TC5(DMA1);
        // buffer wrapped, head back to 0
    }
    if (LL_DMA_IsActiveFlag_HT5(DMA1)) {
        LL_DMA_ClearFlag_HT5(DMA1);
        // half-way point reached, head at RX_BUF_SIZE/2
    }
}
```
DMA 通道映射表 STM32F1 DMA1（pipe 分隔无表头分隔符）：
通道|外设请求
DMA1_Channel1|ADC1
DMA1_Channel2|SPI1_RX / TIM2_CH3
DMA1_Channel3|SPI1_TX / TIM2_CH4
DMA1_Channel4|SPI2_RX / USART1_TX
DMA1_Channel5|SPI2_TX / USART1_RX
DMA1_Channel6|USART2_RX
DMA1_Channel7|USART2_TX

DMA2 通道映射表 STM32F1（注意只有大容量型号才有 DMA2）：
通道|外设请求
DMA2_Channel1|TIM1_CH1 / ADC3
DMA2_Channel2|TIM1_CH2 / SPI1_RX
DMA2_Channel3|TIM1_CH3 / SPI1_TX
DMA2_Channel4|TIM1_CH4 / TIM1_TRIG / TIM1_COM / USART1_TX
DMA2_Channel5|TIM1_UP / USART1_RX

DMA 调试技巧：DMA 传输完成后 TC 标志置位，必须手动清除，否则不会再触发中断，circular 模式下 TC 每次循环到末尾都置位可以用它来知道缓冲区回绕了。HT 半传输中断在传输到一半时触发，配合 TC 可以实现双缓冲 ping-pong，前半段处理时后半段接收，反之亦然。DMA 内存对齐问题：如果设了 word 传输（4 字节）内存地址必须 4 字节对齐否则会 hardfault，用 __attribute__((aligned(4))) 或者 union 强制对齐。Cache 一致性问题：STM32F7/H7 有 D-Cache，DMA 直接访问内存不经过 cache，CPU 读到的是 cache 里的旧数据，必须在 DMA 接收前 SCB_InvalidateDCache_by_Addr 接收后 SCB_CleanDCache_by_Addr，F1/F4 没有 cache 不用管。ESP32 的 DMA 叫 GDMA，配置比 STM32 简单，spi_bus_initialize 第三个参数传 SPI_DMA_CH_AUTO 让系统自动分配 DMA 通道，UART 也可以用 UART_NUM 的 uart_driver_install 配合环形缓冲，ESP32 的 UART 底层已经内置 DMA 不用用户配寄存器。中断和 NVIC 调试是嵌入式进阶必须掌握的，NVIC 是 Nested Vectored Interrupt Controller 嵌套向量中断控制器，Cortex-M 内核自带。NVIC 优先级分抢占优先级 preemption 和子优先级 subpriority，抢占优先级高的可以打断低的正在执行的中断，子优先级只在两个同抢占优先级的中断同时 pending 时决定谁先执行。优先级分组由 SCB->AIRCR 的 PRIGROUP 位决定，STM32 默认 NVIC_PRIORITYGROUP_4 是 4 位全给抢占 0 位给子优先级，共 16 个抢占等级 0-15，0 最高 15 最低。下面这段 STM32 外部中断 EXTI 配置把 PA0 配成下降沿触发按键中断，优先级 6：
```c
// stm32_exti_pa0.c
#include "stm32f1xx_ll_exti.h"
#include "stm32f1xx_ll_bus.h"
#include "stm32f1xx_ll_gpio.h"
#include "stm32f1xx_ll_system.h"
#include "stm32f1xx_ll_utils.h"

volatile uint32_t exti0_count = 0;

void exti0_init(void) {
    LL_APB2_GRP1_EnableClock(LL_APB2_GRP1_PERIPH_GPIOA);
    LL_APB2_GRP1_EnableClock(LL_APB2_GRP1_PERIPH_AFIO);

    // PA0 input pull-up
    LL_GPIO_SetPinMode(GPIOA, LL_GPIO_PIN_0, LL_GPIO_MODE_INPUT);
    LL_GPIO_SetPinPull(GPIOA, LL_GPIO_PIN_0, LL_GPIO_PULL_UP);

    // route PA0 to EXTI line 0
    LL_GPIO_AF_SetEXTISource(LL_GPIO_AF_EXTI_PORTA, LL_GPIO_AF_EXTI_LINE0);

    // configure EXTI line 0: falling edge, interrupt mode
    LL_EXTI_InitTypeDef exti_init = {0};
    exti_init.Line_0_31 = LL_EXTI_LINE_0;
    exti_init.LineCommand = ENABLE;
    exti_init.Mode = LL_EXTI_MODE_INTERRUPT;
    exti_init.Trigger = LL_EXTI_TRIGGER_FALLING;
    LL_EXTI_Init(&exti_init);

    // NVIC: preemption 6, subpriority 0
    NVIC_SetPriority(EXTI0_IRQn, NVIC_EncodePriority(NVIC_GetPriorityGrouping(), 6, 0));
    NVIC_EnableIRQ(EXTI0_IRQn);
}

void EXTI0_IRQHandler(void) {
    if (LL_EXTI_IsActiveFlag_0_31(LL_EXTI_LINE_0)) {
        LL_EXTI_ClearFlag_0_31(LL_EXTI_LINE_0);
        exti0_count++;
        // debounce: simple delay, real code should use timer
        for (volatile int i = 0; i < 100000; i++) {}
    }
}
```
NVIC 优先级分组速查表（tab 分隔）：
分组|抢占位数|子优先级位数|抢占级别数|子级别数|说明
GROUP_0|0|4|1|16|无抢占全靠子优先级
GROUP_1|1|3|2|8
GROUP_2|2|2|4|4
GROUP_3|3|1|8|2
GROUP_4|4|0|16|1|STM32 HAL 默认 全抢占

中断调试技巧：中断里千万不要做耗时操作，不要用 HAL_Delay 这种基于 SysTick 的延时，因为 SysTick 优先级可能比当前中断低导致死锁，中断里只清标志位、设标志位、启动 DMA 然后退出，复杂处理放主循环。EXTI 挂起标志 PR 位必须在 ISR 入口清除否则会反复触发，清除方式是写 1 清零（F1）或者写 PR 寄存器（F4）。按键消抖硬件用 RC 滤波软件用定时器延时 20ms 后再读电平确认。中断嵌套要小心 reentrancy，如果高优先级中断里调用了主循环也在用的函数且该函数有静态变量可能出问题。SysTick 是 Cortex-M 内核自带的 24 位倒计时定时器，STM32 HAL 用它做 1ms 时基，优先级默认最低 15 这样其他中断都能打断它，HAL_Delay 才不会因为高优先级中断卡死而失效。ESP32 的中断是 FreeRTOS 管理的，用 esp_intr_alloc 注册中断，中断里要调用 IRAM_ATTR 修饰的函数（放内部 RAM 防止 flash cache miss 崩溃），不能直接调用 FreeRTOS API 必须用 FromISR 版本，要用 xQueueSendFromISR 和 portYIELD_FROM_ISR 触发任务切换。ESP32 外部中断示例代码：
```c
// esp32_exti_button.c
#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

#define BUTTON_GPIO    GPIO_NUM_0
static QueueHandle_t btn_evt_queue = NULL;

// IRAM_ATTR forces this into IRAM so it survives flash cache disable
static void IRAM_ATTR button_isr_handler(void *arg) {
    uint32_t gpio_num = (uint32_t)arg;
    BaseType_t xHigherPriorityTaskWoken = pdFALSE;
    xQueueSendFromISR(btn_evt_queue, &gpio_num, &xHigherPriorityTaskWoken);
    if (xHigherPriorityTaskWoken) {
        portYIELD_FROM_ISR();
    }
}

static void button_task(void *arg) {
    uint32_t io_num;
    for (;;) {
        if (xQueueReceive(btn_evt_queue, &io_num, portMAX_DELAY)) {
            printf("GPIO[%d] intr, val=%d\n", io_num, gpio_get_level(io_num));
            vTaskDelay(pdMS_TO_TICKS(20)); // debounce
        }
    }
}

void app_main(void) {
    btn_evt_queue = xQueueCreate(10, sizeof(uint32_t));
    xTaskCreate(button_task, "button_task", 2048, NULL, 10, NULL);

    gpio_config_t io_conf = {};
    io_conf.intr_type = GPIO_INTR_NEGEDGE;
    io_conf.mode = GPIO_MODE_INPUT;
    io_conf.pin_bit_mask = (1ULL << BUTTON_GPIO);
    io_conf.pull_up_en = 1;
    gpio_config(&io_conf);

    gpio_install_isr_service(ESP_INTR_FLAG_DEFAULT);
    gpio_isr_handler_add(BUTTON_GPIO, button_isr_handler, (void *)BUTTON_GPIO);
}
```
混合调试杂项记录。时钟树配置是 STM32 的基本功，HSE 外部晶振 8MHz 经过 PLL 倍频到 72MHz（F1）或 168MHz（F407）或 216MHz（F7）或 480MHz（H7），PLL 配置错了所有外设时序都错。下面这段 F1 时钟配置用 HSE 8MHz 倍频到 72MHz：
```c
// stm32f1_clock_72mhz.c
#include "stm32f1xx_ll_rcc.h"
#include "stm32f1xx_ll_system.h"
#include "stm32f1xx_ll_utils.h"
#include "stm32f1xx_ll_bus.h"

void system_clock_72mhz(void) {
    // enable HSE 8MHz external crystal
    LL_RCC_HSE_Enable();
    while (LL_RCC_HSE_IsReady() != 1) {}

    // configure PLL: HSE / 1 * 9 = 72MHz
    LL_RCC_PLL_ConfigDomain_SYS(LL_RCC_PLLSOURCE_HSE_DIV_1, LL_RCC_PLLMUL_9);

    // set flash latency: 72MHz needs 2 wait states
    LL_FLASH_SetLatency(LL_FLASH_LATENCY_2);
    LL_FLASH_EnablePrefetch();

    // enable PLL and wait
    LL_RCC_PLL_Enable();
    while (LL_RCC_PLL_IsReady() != 1) {}

    // switch system clock to PLL
    LL_RCC_SetSysClkSource(LL_RCC_SYS_CLKSOURCE_PLL);
    while (LL_RCC_GetSysClkSource() != LL_RCC_SYS_CLKSOURCE_STATUS_PLL) {}

    // AHB=72MHz, APB1=36MHz, APB2=72MHz
    LL_RCC_SetAHBPrescaler(LL_RCC_SYSCLK_DIV_1);
    LL_RCC_SetAPB1Prescaler(LL_RCC_APB1_DIV_2);
    LL_RCC_SetAPB2Prescaler(LL_RCC_APB2_DIV_1);

    // update SystemCoreClock variable
    LL_SetSystemCoreClock(72000000);
}
```
时钟树速查（pipe 分隔无表头分隔符）：
芯片|HSE|PLL|SYSCLK|AHB|APB1|APB2
STM32F103|8MHz|×9|72MHz|72MHz|36MHz|72MHz
STM32F407|8MHz|×12|168MHz|168MHz|42MHz|84MHz
STM32F746|25MHz|×9|216MHz|216MHz|54MHz|108MHz
STM32H743|25MHz|×18|480MHz|240MHz|120MHz|120MHz

定时器调试，STM32 通用定时器 TIM2-TIM5 是 16/32 位，基本定时器 TIM6/TIM7 是 16 位只能定时不能输出 PWM，高级定时器 TIM1/TIM8 能输出互补 PWM 带死区。下面这段 TIM2 配 1ms 中断的代码，APB1=36MHz 定时器时钟 72MHz（APB1 分频 2 时定时器自动倍频），预分频 71 自动重装载 999 就是 1ms：```c
// stm32_tim2_1ms.c
#include "stm32f1xx_ll_tim.h"
#include "stm32f1xx_ll_bus.h"

volatile uint32_t tick_1ms = 0;

void tim2_init_1ms(void) {
    LL_APB1_GRP1_EnableClock(LL_APB1_GRP1_PERIPH_TIM2);

    // TIM2 on APB1, APB1 prescaler = 2, so timer clock = 72MHz
    LL_TIM_SetPrescaler(TIM2, 71);              // 72MHz / 72 = 1MHz
    LL_TIM_SetAutoReload(TIM2, 999);            // 1MHz / 1000 = 1kHz = 1ms
    LL_TIM_SetCounterMode(TIM2, LL_TIM_COUNTERMODE_UP);
    LL_TIM_SetClockDivision(TIM2, LL_TIM_CLOCKDIVISION_DIV1);

    LL_TIM_EnableIT_UPDATE(TIM2);
    NVIC_SetPriority(TIM2_IRQn, 7);
    NVIC_EnableIRQ(TIM2_IRQn);

    LL_TIM_GenerateEvent_UPDATE(TIM2);          // load shadow regs
    LL_TIM_ClearFlag_UPDATE(TIM2);
    LL_TIM_EnableCounter(TIM2);
}

void TIM2_IRQHandler(void) {
    if (LL_TIM_IsActiveFlag_UPDATE(TIM2)) {
        LL_TIM_ClearFlag_UPDATE(TIM2);
        tick_1ms++;
    }
}
```
PWM 输出调试，TIM1 CH1 输出 1kHz 50% 占空比 PWM 驱动呼吸灯，APB2=72MHz 高级定时器时钟也是 72MHz，预分频 71 自动重装载 999 得到 1kHz，CCR1 = 500 就是 50% 占空比，高级定时器还要使能 MOE 主输出否则 PWM 不输出，这是新手最容易漏的一步。下面这段代码：
```c
// stm32_tim1_pwm_ch1.c
#include "stm32f1xx_ll_tim.h"
#include "stm32f1xx_ll_bus.h"
#include "stm32f1xx_ll_gpio.h"

void tim1_pwm_init(void) {
    LL_APB2_GRP1_EnableClock(LL_APB2_GRP1_PERIPH_TIM1);
    LL_APB2_GRP1_EnableClock(LL_APB2_GRP1_PERIPH_GPIOA);

    // PA8 = TIM1_CH1, AF push-pull 50MHz
    LL_GPIO_SetPinMode(GPIOA, LL_GPIO_PIN_8, LL_GPIO_MODE_ALTERNATE);
    LL_GPIO_SetPinOutputType(GPIOA, LL_GPIO_PIN_8, LL_GPIO_OUTPUT_PUSHPULL);
    LL_GPIO_SetPinSpeed(GPIOA, LL_GPIO_PIN_8, LL_GPIO_SPEED_FREQ_HIGH);

    LL_TIM_SetPrescaler(TIM1, 71);
    LL_TIM_SetAutoReload(TIM1, 999);          // 1kHz
    LL_TIM_SetCounterMode(TIM1, LL_TIM_COUNTERMODE_UP);

    LL_TIM_OC_SetCompareCH1(TIM1, 500);       // 50% duty
    LL_TIM_OC_SetMode(TIM1, LL_TIM_CHANNEL_CH1, LL_TIM_OCMODE_PWM1);
    LL_TIM_OC_EnablePreload(TIM1, LL_TIM_CHANNEL_CH1);
    LL_TIM_OC_SetPolarity(TIM1, LL_TIM_CHANNEL_CH1, LL_TIM_OCPOLARITY_HIGH);

    LL_TIM_EnableARRPreload(TIM1);
    LL_TIM_EnableAllOutputs(TIM1);            // MOE: critical for TIM1/TIM8!
    LL_TIM_CC_EnableChannel(TIM1, LL_TIM_CHANNEL_CH1);
    LL_TIM_GenerateEvent_UPDATE(TIM1);
    LL_TIM_EnableCounter(TIM1);
}

void tim1_set_duty(uint16_t promille) {
    // promille in [0, 1000], maps to [0, 1000] counts (ARR=999)
    if (promille > 1000) promille = 1000;
    LL_TIM_OC_SetCompareCH1(TIM1, promille);
}
```
ADC 调试，STM32F1 ADC1 12 位精度转换 PA0 上的电池电压，ADC 时钟不能超过 14MHz，APB2=72MHz 分频 6 得到 12MHz，采样时间 55.5 周期总转换时间 (55.5+12.5)/12MHz = 5.67us，参考电压一般接 VREF+ = 3.3V，12 位满量程 4095 对应 3.3V。下面这段代码用软件触发单次转换：
```c
// stm32_adc1_pa0.c
#include "stm32f1xx_ll_adc.h"
#include "stm32f1xx_ll_bus.h"
#include "stm32f1xx_ll_gpio.h"

void adc1_init(void) {
    LL_APB2_GRP1_EnableClock(LL_APB2_GRP1_PERIPH_ADC1);
    LL_APB2_GRP1_EnableClock(LL_APB2_GRP1_PERIPH_GPIOA);

    // PA0 analog input
    LL_GPIO_SetPinMode(GPIOA, LL_GPIO_PIN_0, LL_GPIO_MODE_ANALOG);

    // ADC clock = PCLK2/6 = 12MHz
    LL_RCC_SetADCClockSource(LL_RCC_ADC_CLKSRC_PCLK2_DIV_6);

    LL_ADC_SetSequencersScanMode(ADC1, LL_ADC_SEQ_SCAN_DISABLE);
    LL_ADC_REG_SetSequencerLength(ADC1, LL_ADC_REG_SEQ_SCAN_DISABLE_1RANK);
    LL_ADC_REG_SetSequencerRanks(ADC1, LL_ADC_REG_RANK_1, LL_ADC_CHANNEL_0);
    LL_ADC_SetChannelSamplingTime(ADC1, LL_ADC_CHANNEL_0, LL_ADC_SAMPLINGTIME_55CYCLES_5);

    LL_ADC_Enable(ADC1);
    // ADC stabilization
    for (volatile int i = 0; i < 1000; i++) {}
    LL_ADC_StartCalibration(ADC1);
    while (LL_ADC_IsCalibrationOnGoing(ADC1)) {}
}

uint16_t adc1_read_pa0(void) {
    LL_ADC_REG_SetSequencerRanks(ADC1, LL_ADC_REG_RANK_1, LL_ADC_CHANNEL_0);
    LL_ADC_REG_StartConversionSWStart(ADC1);
    while (!LL_ADC_IsActiveFlag_EOS(ADC1)) {}
    LL_ADC_ClearFlag_EOS(ADC1);
    return LL_ADC_REG_ReadConversionData12(ADC1); // 0..4095
}

float adc_to_voltage(uint16_t raw) {
    return (raw * 3.3f) / 4095.0f;
}
```
看门狗调试，独立看门狗 IWDG 用 LSI 40kHz 时钟，超时时间 = (4 * 2^PR) * (RL+1) / 40000 秒，PR=2 分频 16 RL=4095 超时约 1.6384s，喂狗必须在超时前调用 LL_IWDG_ReloadCounter，否则系统复位。代码：
```c
// stm32_iwdg.c
#include "stm32f1xx_ll_iwdg.h"

void iwdg_init_1s6(void) {
    LL_IWDG_Enable(IWDG);
    LL_IWDG_EnableWriteAccess(IWDG);
    LL_IWDG_SetPrescaler(IWDG, LL_IWDG_PRESCALER_16); // 40kHz/16 = 2.5kHz
    LL_IWDG_SetReloadCounter(IWDG, 4095);             // 4096/2.5kHz = 1.6384s
    while (!LL_IWDG_IsReady(IWDG)) {}
    LL_IWDG_ReloadCounter(IWDG);
}

void iwdg_feed(void) {
    LL_IWDG_ReloadCounter(IWDG);
}
```
窗口看门狗 WWDG 比 IWDG 复杂，有窗口概念：计数器必须在窗口内喂狗，太早喂或者太晚喂都会复位，用于检测软件跑飞但还在跑的情况。WWDG 时钟来自 APB1 经过 4096 分频再经过 2^WDGTB 分频，超时公式 T = (4096 * 2^WDGTB * (T[5:0]+1)) / fPCLK1。CAN 总线调试，CAN 总线两根线 CAN_H 和 CAN_L 差分信号，必须接 120Ω 终端电阻在总线两端，没终端电阻信号反射会导致通信失败。STM32 bxCAN 支持标准帧 11 位 ID 和扩展帧 29 位 ID，波特率计算：bit time = SYNC + TS1 + TS2 段，每段是若干时间份额 TQ，TQ = (BRP+1)/fPCLK。500kbps 在 36MHz APB1 上 BRP=8 TQ=1us 不对，实际 BRP=4 一个 TQ = 4/36 = 0.111us，bit time = 1+8+3=12 TQ = 1.33us 对应 750kHz，调到 1+7+3=11 TQ 不行，500kbps 时 bit time = 2us，2us/0.111us = 18 TQ，分 1+14+3 = 18 TQ。下面这段 CAN1 配置 500kbps 标准帧发送：
```c
// stm32_can1_500k.c
#include "stm32f1xx_ll_can.h"
#include "stm32f1xx_ll_bus.h"
#include "stm32f1xx_ll_gpio.h"

void can1_init_500kbps(void) {
    LL_APB1_GRP1_EnableClock(LL_APB1_GRP1_PERIPH_CAN1);
    LL_APB2_GRP1_EnableClock(LL_APB2_GRP1_PERIPH_GPIOA);
    LL_APB2_GRP1_EnableClock(LL_APB2_GRP1_PERIPH_AFIO);

    // PA11=CAN_RX, PA12=CAN_TX
    LL_GPIO_SetPinMode(GPIOA, LL_GPIO_PIN_12, LL_GPIO_MODE_ALTERNATE);
    LL_GPIO_SetPinMode(GPIOA, LL_GPIO_PIN_11, LL_GPIO_MODE_INPUT);
    LL_GPIO_SetPinOutputType(GPIOA, LL_GPIO_PIN_12, LL_GPIO_OUTPUT_PUSHPULL);
    LL_GPIO_SetPinSpeed(GPIOA, LL_GPIO_PIN_12, LL_GPIO_SPEED_FREQ_HIGH);

    LL_CAN_LeaveInitMode(CAN1);
    LL_CAN_SetMode(CAN1, LL_CAN_MODE_NORMAL);

    // 500 kbps: SJW=1, TS1=14, TS2=3, BRP=3 (36MHz / (1+14+3)/4 = 500k)
    LL_CAN_SetBitTiming(CAN1, 3, LL_CAN_TIME_SEGMENT1_14, LL_CAN_TIME_SEGMENT2_3, LL_CAN_SYNC_JUMP_WIDTH_1);

    LL_CAN_Enable(CAN1);
}
```
CAN 调试技巧：用 CAN 分析仪抓总线，确认波特率，确认终端电阻，确认 ID 不冲突，标准帧 DLC 数据长度 0-8 字节，扩展帧 ID 29 位但 DLC 还是 0-8 字节，CAN FD 才能传 64 字节。Flash 调试，STM32 内部 flash 读写保护，写之前必须解锁，HAL_FLASH_Unlock 解锁后才能写，写完 HAL_FLASH_Lock 锁定。下面这段代码擦除 page 127（F103C8 最后一页 0x0801FC00）然后写入一个 word，用来存非易失配置：
```c
// stm32_flash_write.c
#include "stm32f1xx_ll_flash.h"

#define CONFIG_PAGE_ADDR    0x0801FC00UL

int flash_write_config(uint32_t offset, uint32_t data) {
    LL_FLASH_Unlock();

    LL_FLASH_SetProgramSize(LL_FLASH_PGSIZE_HALFWORD);
    FLASH_EraseInitTypeDef erase = {};
    erase.TypeErase = FLASH_TYPEERASE_PAGES;
    erase.PageAddress = CONFIG_PAGE_ADDR;
    erase.NbPages = 1;
    uint32_t err = 0;
    if (LL_FLASH_Erase(&erase, &err) != LL_OK) {
        LL_FLASH_Lock();
        return -1;
    }

    LL_FLASH_Program_Word(LL_FLASH_PGSIZE_WORD, CONFIG_PAGE_ADDR + offset, data);
    while (LL_FLASH_IsActiveFlag_BSY(FLASH)) {}

    if (LL_FLASH_IsActiveFlag_WRPRTERR(FLASH) || LL_FLASH_IsActiveFlag_PGERR(FLASH)) {
        LL_FLASH_ClearFlag_WRPRTERR(FLASH);
        LL_FLASH_ClearFlag_PGERR(FLASH);
        LL_FLASH_Lock();
        return -2;
    }

    LL_FLASH_Lock();
    return 0;
}

uint32_t flash_read_config(uint32_t offset) {
    return *(volatile uint32_t *)(CONFIG_PAGE_ADDR + offset);
}
```
低功耗调试，STM32 STOP 模式功耗最低约 20uA 但所有时钟停了只保留外部中断和 RTC 唤醒，唤醒后要重新配 PLL 因为 PLL 在 STOP 模式自动关，下面这段进 STOP 模式并用 PA0 唤醒的代码：
```c
// stm32_stop_mode.c
#include "stm32f1xx_ll_pwr.h"
#include "stm32f1xx_ll_exti.h"
#include "stm32f1xx_ll_rcc.h"

void enter_stop_mode(void) {
    LL_APB1_GRP1_EnableClock(LL_APB1_GRP1_PERIPH_PWR);
    LL_PWR_SetPowerMode(LL_PWR_MODE_STOP_MAINREGU);
    LL_PWR_EnableWakeUpPin(LL_PWR_WAKEUP_PIN1);

    // clear EXTI line 0 pending flag before sleep
    LL_EXTI_ClearFlag_0_31(LL_EXTI_LINE_0);

    // SEV on pending, WFI
    __WFI();

    // after wake-up: reconfigure PLL (STOP mode disables PLL)
    // call system_clock_72mhz() here, omitted for brevity
}
```
ESP32 低功耗用 deep sleep 模式，RTC 内存保留数据，功耗约 10uA，唤醒源有定时器 RTC、外部 GPIO (EXT0/EXT1)、触摸传感器、ULP 协处理器。下面这段 ESP32 deep sleep 5 秒后唤醒代码：
```c
// esp32_deep_sleep.c
#include "esp_sleep.h"
#include "esp_log.h"

void enter_deep_sleep_5s(void) {
    esp_sleep_enable_timer_wakeup(5 * 1000000ULL); // 5 seconds in us
    esp_log("entering deep sleep");
    esp_deep_sleep_start();
}
```
启动调试，STM32 启动从 0x08000000 取初始栈指针和复位向量，启动文件 startup_stm32f103xb.s 里定义了 Reset_Handler 调用 SystemInit 配时钟再调用 __libc_init_array 初始化 C 库最后调 main，下面这段启动文件关键部分节选：
```asm
; stm32 startup excerpt
                SECTION .text:CODE:NOROOT(2)
Reset_Handler   PROC
                EXPORT  Reset_Handler             [WEAK]
                IMPORT  __main
                IMPORT  SystemInit
                LDR     R0, =SystemInit
                BLX     R0
                LDR     R0, =__main
                BX      R0
                ENDP
```
链接脚本 ld 文件定义内存布局，FLASH 起始 0x08000000 长度 256KB（C8 是 64KB），RAM 起始 0x20000000 长度 20KB（C8 是 20KB），栈顶 _estack 放在 RAM 末尾 0x20005000。堆和栈相向生长，链接脚本里 _Min_Heap_Size 和 _Min_Stack_Size 必须保证不溢出，否则 hardfault 但很难定位。RTOS 调试，FreeRTOS 任务栈大小以 word 为单位，STM32 一个 word 是 4 字节，configMINIMAL_STACK_SIZE 一般 128 word = 512 字节，任务里调用 printf 或 HAL 函数栈要开大点 256 word 以上，任务栈溢出会触发 configCHECK_FOR_STACK_OVERFLOW 钩子，下面这段创建一个 blinky 任务：
```c
// freertos_blinky.c
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "stm32f1xx_ll_gpio.h"

static void blink_task(void *arg) {
    (void)arg;
    for (;;) {
        LL_GPIO_TogglePin(GPIOA, LL_GPIO_PIN_5);
        vTaskDelay(pdMS_TO_TICKS(500));
    }
}

void freertos_start(void) {
    xTaskCreate(blink_task, "blink", 256, NULL, 2, NULL);
    vTaskStartScheduler();
}
```
任务优先级 0 最低 configMAX_PRIORITIES-1 最高，相同优先级时间片轮转，FreeRTOS 调度器在 SysTick 里做上下文切换，SysTick 优先级必须设最低否则会断言失败。临界区 taskENTER_CRITICAL 关中断 taskEXIT_CRITICAL 开中断，临界区里不能调用阻塞 API，临界区太长会丢中断。互斥量 xSemaphoreCreateMutex 有优先级继承防止优先级反转，二值信号量 xSemaphoreCreateBinary 没有优先级继承。下面这段生产者消费者用队列：
```c
// freertos_queue.c
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

static QueueHandle_t data_queue;

void producer(void *arg) {
    (void)arg;
    int v = 0;
    for (;;) {
        xQueueSend(data_queue, &v, portMAX_DELAY);
        v++;
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}

void consumer(void *arg) {
    (void)arg;
    int v;
    for (;;) {
        if (xQueueReceive(data_queue, &v, portMAX_DELAY) == pdPASS) {
            // process v
        }
    }
}

void queue_demo_start(void) {
    data_queue = xQueueCreate(8, sizeof(int));
    xTaskCreate(producer, "prod", 128, NULL, 3, NULL);
    xTaskCreate(consumer, "cons", 128, NULL, 2, NULL);
    vTaskStartScheduler();
}
```
硬件 SPI 驱动 W25Q64 flash 完整示例，包含读 ID、读状态、写使能、页编程、扇区擦除、读数据，SPI 时钟 36MHz，扇区大小 4KB，页大小 256 字节，写之前必须发写使能 0x06，写完要等状态寄存器 BUSY 位清零，页编程地址必须页对齐否则会回卷覆盖：
```c
// w25q64_driver.c
#include "stm32f1xx_ll_spi.h"
#include "stm32f1xx_ll_gpio.h"

#define W25Q_CS_LOW()    LL_GPIO_ResetOutputPin(GPIOA, LL_GPIO_PIN_4)
#define W25Q_CS_HIGH()   LL_GPIO_SetOutputPin(GPIOA, LL_GPIO_PIN_4)

extern uint8_t spi1_transfer(uint8_t tx);

uint16_t w25q_read_id(void) {
    uint16_t id;
    W25Q_CS_LOW();
    spi1_transfer(0x90);
    spi1_transfer(0x00);
    spi1_transfer(0x00);
    spi1_transfer(0x00);
    id = (spi1_transfer(0x00) << 8);
    id |= spi1_transfer(0x00);
    W25Q_CS_HIGH();
    return id; // 0xEF16 expected
}

void w25q_wait_busy(void) {
    W25Q_CS_LOW();
    spi1_transfer(0x05); // read status reg 1
    while (spi1_transfer(0x00) & 0x01) {}
    W25Q_CS_HIGH();
}

void w25q_write_enable(void) {
    W25Q_CS_LOW();
    spi1_transfer(0x06);
    W25Q_CS_HIGH();
}

void w25q_page_program(uint32_t addr, const uint8_t *data, uint16_t len) {
    if (len > 256) len = 256;
    w25q_write_enable();
    W25Q_CS_LOW();
    spi1_transfer(0x02); // page program
    spi1_transfer((addr >> 16) & 0xFF);
    spi1_transfer((addr >> 8) & 0xFF);
    spi1_transfer(addr & 0xFF);
    for (uint16_t i = 0; i < len; i++) {
        spi1_transfer(data[i]);
    }
    W25Q_CS_HIGH();
    w25q_wait_busy();
}

void w25q_sector_erase(uint32_t addr) {
    w25q_write_enable();
    W25Q_CS_LOW();
    spi1_transfer(0x20); // sector erase 4KB
    spi1_transfer((addr >> 16) & 0xFF);
    spi1_transfer((addr >> 8) & 0xFF);
    spi1_transfer(addr & 0xFF);
    W25Q_CS_HIGH();
    w25q_wait_busy();
}

void w25q_read(uint32_t addr, uint8_t *buf, uint32_t len) {
    W25Q_CS_LOW();
    spi1_transfer(0x03); // read data
    spi1_transfer((addr >> 16) & 0xFF);
    spi1_transfer((addr >> 8) & 0xFF);
    spi1_transfer(addr & 0xFF);
    for (uint32_t i = 0; i < len; i++) {
        buf[i] = spi1_transfer(0x00);
    }
    W25Q_CS_HIGH();
}
```
I2C 驱动 SSD1306 OLED 完整示例，128x64 单色屏，I2C 地址 0x3C，初始化序列要按手册发一堆配置命令，显存是 8 页每页 128 列，每个字节代表 8 个垂直像素，写显存前要先设置页地址和列地址：
```c
// ssd1306_i2c.c
#include "stm32f4xx_ll_i2c.h"

#define SSD1306_ADDR   0x3C
extern uint8_t i2c1_read_reg(uint8_t dev, uint8_t reg);
extern void i2c1_write_reg(uint8_t dev, uint8_t reg, uint8_t val);

static uint8_t oled_fb[8][128];

static void ssd1306_cmd(uint8_t c) {
    // Co=0, D/C#=0 -> command
    i2c1_write_reg(SSD1306_ADDR, 0x00, c);
}

static void ssd1306_data(uint8_t d) {
    // Co=0, D/C#=1 -> data
    i2c1_write_reg(SSD1306_ADDR, 0x40, d);
}

void ssd1306_init(void) {
    ssd1306_cmd(0xAE); // display off
    ssd1306_cmd(0x20); ssd1306_cmd(0x00); // horizontal addressing
    ssd1306_cmd(0x40); // start line 0
    ssd1306_cmd(0xA1); // segment remap
    ssd1306_cmd(0xA8); ssd1306_cmd(0x3F); // mux ratio 64
    ssd1306_cmd(0xC8); // COM scan dir remap
    ssd1306_cmd(0xD3); ssd1306_cmd(0x00); // display offset 0
    ssd1306_cmd(0xD5); ssd1306_cmd(0x80); // clock divide
    ssd1306_cmd(0xD9); ssd1306_cmd(0xF1); // pre-charge
    ssd1306_cmd(0xDA); ssd1306_cmd(0x12); // com pins
    ssd1306_cmd(0xDB); ssd1306_cmd(0x40); // vcomh deselect
    ssd1306_cmd(0x8D); ssd1306_cmd(0x14); // charge pump on
    ssd1306_cmd(0xA4); // display resume RAM
    ssd1306_cmd(0xA6); // normal display
    ssd1306_cmd(0xAF); // display on
}

void ssd1306_flush(void) {
    for (uint8_t p = 0; p < 8; p++) {
        ssd1306_cmd(0xB0 | p);            // page address
        ssd1306_cmd(0x00 | 0);            // lower col
        ssd1306_cmd(0x10 | 0);            // upper col
        for (uint8_t c = 0; c < 128; c++) {
            ssd1306_data(oled_fb[p][c]);
        }
    }
}

void ssd1306_clear(void) {
    for (uint8_t p = 0; p < 8; p++)
        for (uint8_t c = 0; c < 128; c++)
            oled_fb[p][c] = 0x00;
}

void ssd1306_pixel(uint8_t x, uint8_t y, uint8_t on) {
    if (x >= 128 || y >= 64) return;
    if (on) oled_fb[y / 8][x] |= (1 << (y & 7));
    else    oled_fb[y / 8][x] &= ~(1 << (y & 7));
}
```
ESP32 I2C 驱动 BMP280 气压传感器示例，地址 0x76，读 chip id 寄存器 0xD0 应该返回 0x58，读校准参数从 0x88 开始 24 字节，读原始温度气压 0xF7 开始 6 字节，然后按手册公式补偿：
```c
// esp32_bmp280.c
#include "driver/i2c.h"
#include "esp_log.h"

#define BMP280_ADDR       0x76
#define BMP280_REG_ID     0xD0
#define BMP280_REG_RESET  0xE0
#define BMP280_REG_CTRL   0xF4
#define BMP280_REG_DATA   0xF7
#define I2C_MASTER_NUM    I2C_NUM_0

static const char *TAG = "bmp280";

esp_err_t bmp280_read_reg(uint8_t reg, uint8_t *val) {
    i2c_cmd_handle_t cmd = i2c_cmd_link_create();
    i2c_master_start(cmd);
    i2c_master_write_byte(cmd, (BMP280_ADDR << 1) | I2C_MASTER_WRITE, true);
    i2c_master_write_byte(cmd, reg, true);
    i2c_master_start(cmd);
    i2c_master_write_byte(cmd, (BMP280_ADDR << 1) | I2C_MASTER_READ, true);
    i2c_master_read_byte(cmd, val, I2C_MASTER_LAST_NACK);
    i2c_master_stop(cmd);
    esp_err_t ret = i2c_master_cmd_begin(I2C_MASTER_NUM, cmd, pdMS_TO_TICKS(100));
    i2c_cmd_link_delete(cmd);
    return ret;
}

esp_err_t bmp280_init(void) {
    uint8_t id = 0;
    esp_err_t ret = bmp280_read_reg(BMP280_REG_ID, &id);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "i2c read failed");
        return ret;
    }
    ESP_LOGI(TAG, "chip id = 0x%02x (expect 0x58)", id);
    if (id != 0x58) return ESP_ERR_NOT_FOUND;
    return ESP_OK;
}
```
调试断点技巧，用 SWD 接 ST-Link，Keil 或 STM32CubeIDE 里打断点，看寄存器窗口，看外设寄存器视图能直接看到 GPIOA->MODER 的值确认配置生效，反汇编窗口看代码是否被优化掉，watch 窗口加变量但要加 volatile 否则编译器优化后看不到变化。RTOS 调试要开 FreeRTOS 的 task aware 调试，能在 IDE 里看所有任务状态和栈使用率。Tracealyzer 或 SystemView 抓 trace 能可视化任务调度时序。性能分析用 DWT 周期计数器，CYCCNT 寄存器记录 CPU 周期数，测函数执行时间：
```c
// dwt_cycle_counter.c
#include <stdint.h>

#define DWT_CYCCNT   (*(volatile uint32_t *)0xE0001004)
#define DWT_CONTROL  (*(volatile uint32_t *)0xE0001000)
#define DEMCR        (*(volatile uint32_t *)0xE000EDFC)

void dwt_enable(void) {
    DEMCR |= (1 << 24);            // TRCENA
    DWT_CYCCNT = 0;
    DWT_CONTROL |= (1 << 0);       // CYCCNTENA
}

uint32_t dwt_cycle_count(void) {
    return DWT_CYCCNT;
}

uint32_t measure_us(uint32_t cycles, uint32_t cpu_mhz) {
    return cycles / cpu_mhz;
}
```
GPIO 速率配置 F1 有 2/10/50MHz 三档，F4 用 OSPEEDR 寄存器有 4 档 low/medium/high/very high，速率越高边沿越陡驱动能力越强但 EMI 也越大，一般 SPI SCLK 36MHz 以上要 high，I2C 100kHz 用 medium 就够，按键输入随便。F1 的 GPIO 速率其实是 slew rate 控制，不是输出电流能力，输出电流能力由输出驱动管决定固定 25mA sink / 3mA source 典型值，不要用 GPIO 直接驱动大电流负载要加三极管或 MOSFET。

最后一段无结构混合记录，调试时要养成习惯先确认电源再确认时钟再确认引脚再确认外设配置，电源纹波大用示波器交流耦合测 VDD 和 GND 之间应该小于 50mV 峰峰值，时钟用示波器测 HSE 引脚确认起振，晶振负载电容不对会不起振或者频率偏，8MHz 晶振典型负载电容 22pF 计算公式 CL = (C1*C2)/(C1+C2) + Cstray，Cstray 约 3-5pF。复位脚 NRST 必须接 100nF 到 GND 和 10kΩ 到 VDD，否则上电复位不可靠。BOOT0 BOOT1 脚决定启动模式，BOOT0=0 从 flash 启动正常，BOOT0=1 BOOT1=1 从 RAM 启动，BOOT0=1 BOOT1=0 从系统存储器启动串口下载模式，STM32 出厂烧录 boot loader 在系统存储器里支持 USART1/USART2/USB/CAN/I2C 烧录。ESP32 启动 mode 由 strapping pin GPIO0 GPIO2 GPIO12 GPIO15 决定，下载模式 GPIO0=0 GPIO2=0 GPIO12=0 GPIO15=0，flash 启动模式 GPIO0=1 其他默认，ESP32 上电瞬间这些引脚电平被采样所以不能接强下拉否则进不了 flash 启动。烧录后用 esptool.py --port COMx --baud 921600 write_flash 0x10000 firmware.bin 把应用烧到 0x10000 偏移，bootloader 在 0x1000，分区表在 0x8000，每个分区偏移和大小由 partitions.csv 决定。OTA 升级用 esp_ota_* API 写 ota_1 分区后切 boot partition 重启。调试 ESP32 用 esptool.py --port COMx --baud 921600 --chip esp32 monitor 看串口输出，或者用 idf.py monitor 自动复位自动解 panic backtrace 地址，backtrace 解析要 addr2line 工具配合 elf 文件，命令 xtensa-esp32-elf-addr2line -pfiaC -e firmware.elf 0x400d1234。GPIO ESP32 12 接 flash 不能用，strapping pin 汇总表：
引脚|上电默认|启动模式作用|运行时能否用
GPIO0|INPUT|下载模式选择|可以但要小心
GPIO2|INPUT PULL-DOWN|下载模式辅助|可以
GPIO12|INPUT PULL-DOWN|flash 电压选择 0=3.3V|慎用 影响 flash
GPIO15|INPUT PULL-UP|boot log 输出|可以
GPIO4|无|无|自由用
GPIO5|无|无|自由用 上电瞬间输出 PWM

USB CDC 虚拟串口调试，STM32F1 的 USB 全速 12Mbps，PA11=USB_DM PA12=USB_DP，必须接 1.5kΩ 上拉到 3.3V（D+）让主机识别为全速设备，STM32 内部有这个上拉电阻软件控制 USB 控制寄存器的 D+ pull-up 位。USB 描述符配置正确才能枚举，设备描述符 18 字节包含 VID PID 版本，配置描述符 9 字节加上接口描述符 9 字节加端点描述符 7 字节，CDC 类还要加 IAD 头 8 字节和功能描述符。STM32 USB CDC 例程用 ST 的 USB Device Library，调 USBD_Init 初始化，CDC 发送数据 USBD_CDC_SetTxBuffer 和 USBD_CDC_TransmitPacket。下面这段 PC 端 Python 读 USB CDC 的代码用 pyserial：
```python
# pc_read_usb_cdc.py
import serial
import struct
import time

def open_port(port, baud=115200, timeout=1.0):
    ser = serial.Serial(port, baud, timeout=timeout)
    return ser

def read_stream(ser, n=256):
    data = ser.read(n)
    return data

def parse_frame(data):
    if len(data) < 4:
        return None
    magic, length = struct.unpack("<HH", data[:4])
    if magic != 0xA55A:
        return None
    payload = data[4:4 + length]
    crc = struct.unpack("<H", data[4 + length:6 + length])[0]
    calc = sum(payload) & 0xFFFF
    if crc != calc:
        print(f"CRC mismatch: got {crc:#06x} calc {calc:#06x}")
        return None
    return payload

def main():
    ser = open_port("COM5", 921600)
    try:
        while True:
            data = read_stream(ser, 256)
            if data:
                frame = parse_frame(data)
                if frame is not None:
                    print(f"rx frame {len(frame)} bytes: {frame.hex()}")
    except KeyboardInterrupt:
        pass
    finally:
        ser.close()

if __name__ == "__main__":
    main()
```
OTA 升级 STM32 实现思路，bootloader 在 0x08000000 占 16KB，app1 在 0x08004000 占 220KB，app2 在 0x0803C000 占 220KB，bootloader 启动读 flash 末尾的标志位决定跳 app1 还是 app2，升级时把新固件写 app2 然后置标志位切 app2 重启，下次升级写 app1，双 bank 切换保证升级失败能回滚。bootloader 跳 app 前要关中断、关外设、复位 SysTick、设置 VTOR 指向 app 的中断向量表、设置 MSP、跳转。下面这段 bootloader 跳转代码：
```c
// stm32_bootloader_jump.c
#include "stm32f1xx.h"

#define APP1_ADDR   0x08004000UL
#define APP2_ADDR   0x0803C000UL

typedef void (*app_entry_t)(void);

void jump_to_app(uint32_t app_addr) {
    uint32_t app_sp = *(volatile uint32_t *)app_addr;
    uint32_t app_pc = *(volatile uint32_t *)(app_addr + 4);

    // disable interrupts
    __disable_irq();

    // reset SysTick
    SysTick->CTRL = 0;
    SysTick->LOAD = 0;
    SysTick->VAL = 0;

    // disable all NVIC interrupts
    for (int i = 0; i < 8; i++) {
        NVIC->ICER[i] = 0xFFFFFFFF;
        NVIC->ICPR[i] = 0xFFFFFFFF;
    }

    // set vector table
    SCB->VTOR = app_addr;

    // set stack pointer
    __set_MSP(app_sp);

    // jump
    app_entry_t app = (app_entry_t)app_pc;
    app();
}

void bootloader_main(void) {
    uint32_t active = *(volatile uint32_t *)0x0803F000UL;
    if (active == 0xDEADBEEF) {
        jump_to_app(APP2_ADDR);
    } else {
        jump_to_app(APP1_ADDR);
    }
    // should never reach here
    while (1) {}
}
```
电源管理调试，LDO 选型 AMS1117-3.3 输出 3.3V 最大 800mA 但压差 1.1V 输入要 4.4V 以上才稳定，压差大发热严重效率低，电池供电用低压差 LDO 如 MCP1700 压差 178mV 或者用 DCDC 降压 TPS5430 效率 90% 以上。去耦电容每个电源脚必须就近放 100nF 陶瓷电容，电源入口放 10uF 钽电容，去耦电容离芯片电源脚越近越好走线短而粗。地平面完整不要分割否则回流路径变长 EMI 增加。模拟地数字地单点连接用磁珠或 0Ω 电阻，ADC 参考电压 VREF+ 要单独去耦 100nF + 1uF。示波器探头的地线夹要尽量短否则电感大测到的纹波是假的。逻辑分析仪采样率至少是信号频率的 4 倍，抓 SPI 36MHz 要 144MHz 以上采样率，否则波形失真。调试时先简单后复杂，先点亮 LED 再调 UART 打印再调 SPI/I2C 再调 DMA 再调 RTOS，每步验证再进下一步。GPIO LED 闪烁是 hello world，能闪说明时钟电源启动都没问题，再串口打印能输出说明时钟分频和波特率都对，再 SPI 读 flash ID 能读到说明 SPI 配置对，逐步推进不要一上来就跑全套功能出问题没法定位。
