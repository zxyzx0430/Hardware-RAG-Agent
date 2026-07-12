# ARM Cortex-M 中断与异常处理详解

> 本文档系统讲解 ARM Cortex-M（M0/M0+/M3/M4/M7/M23/M33）系列处理器的中断与异常处理机制，覆盖异常模型、NVIC 嵌套向量中断控制器、优先级分组、中断延迟、尾链与迟来优化、向量表、故障处理、FreeRTOS 集成、外设中断实战、调试与性能优化等内容，包含大量可直接复用的 C 代码与汇编示例，面向嵌入式实时系统开发。

---

## 目录

1. Cortex-M 异常模型概述
2. NVIC 嵌套向量中断控制器
3. 中断延迟与尾链、迟来机制
4. 异常类型详解
5. 中断向量表与重定位
6. 优先级设计与分组策略
7. 中断嵌套与抢占机制
8. FreeRTOS 与中断管理
9. 外设中断实战
10. 中断调试技术
11. 中断性能优化
12. 常见问题 FAQ
13. 不同 MCU 中断实现对比

附录 A-P：寄存器速查、向量表模板、优先级分组表、故障状态解码、调试命令、Code Review 清单等。

---

## 1. Cortex-M 异常模型概述

### 1.1 异常与中断的概念

在 ARM Cortex-M 架构中，**异常（Exception）** 是处理器对内部或外部事件的统一响应机制。中断（Interrupt）是异常的一个子集，特指由外部（外设或 NMI）触发的异常。Cortex-M 把所有打断正常执行流的事件都纳入异常模型统一管理：

- **系统异常**：Reset、NMI、HardFault、MemManage、BusFault、UsageFault、SVCall、PendSV、SysTick 等，由内核产生。
- **外部中断（IRQ）**：由芯片厂商外设（USART、TIM、DMA、GPIO 等）产生，编号从 16 起，最多 240 个。

这种统一模型的好处是：所有异常共用一套优先级机制、共用一张向量表、共用一套压栈/出栈机制，开发者只需掌握一套编程模型即可处理所有中断源。

### 1.2 Cortex-M 各型号异常能力对比

| 型号 | 架构 | 外部中断数 | 优先级位数 | 实现位数 | 备注 |
|------|------|-----------|-----------|---------|------|
| Cortex-M0 | ARMv6-M | 1-32 | 2（4级） | 固定 | 无尾链优化有限 |
| Cortex-M0+ | ARMv6-M | 1-32 | 2（4级） | 固定 | M0 改进版，有 I/O 端口 |
| Cortex-M1 | ARMv6-M | 1-32 | 2（4级） | 固定 | FPGA 优化 |
| Cortex-M3 | ARMv7-M | 1-240 | 3-8（8-256级） | 可配置 | 主流，含尾链/迟来 |
| Cortex-M4 | ARMv7E-M | 1-240 | 3-8 | 可配置 | M3 + DSP/FPU |
| Cortex-M7 | ARMv7E-M | 1-240 | 3-8 | 可配置 | 双发射，L1 缓存 |
| Cortex-M23 | ARMv8-M | 1-240 | 2-8 | 可配置 | TrustZone 基线 |
| Cortex-M33 | ARMv8-M | 1-480 | 3-8 | 可配置 | TrustZone + FPU |

注意优先级位数为**实现位数**，即芯片厂商可选实现 3-8 位。STM32F4 实现 4 位（16 级），STM32H7 实现 4 位，STM32G4 也是 4 位。查询方法：

```c
// Query implemented priority bits at runtime
uint32_t priority_group = NVIC_GetPriorityGrouping();
uint32_t priority_bits = __NVIC_PRIO_BITS;  // From device header, e.g. 4 for STM32F4

// Runtime probe: write 0xFF to a priority register, read back
NVIC_SetPriority(USART1_IRQn, 0xFF);
uint8_t readback = NVIC_GetPriority(USART1_IRQn);
uint8_t implemented_bits = 0;
uint8_t tmp = readback;
while (tmp) { implemented_bits += (tmp & 1); tmp >>= 1; }
printf("Implemented priority bits: %d\n", implemented_bits);
// For STM32F4 (4 bits), readback will be 0xF0 (0xFF rounded to 4 MSBs)
```

### 1.3 处理器模式与特权级别

Cortex-M 处理器有两个执行模式与两个特权级别，理解它们对中断设计至关重要：

| 模式 | 特权级别 | 使用的栈 | 进入条件 |
|------|---------|---------|---------|
| 线程模式（Thread） | 特权 | MSP 或 PSP | Reset 后默认 |
| 线程模式（Thread） | 非特权 | PSP | CONTROL.nPRIV=1 |
| 处理模式（Handler） | 特权 | MSP | 异常进入 |

- **线程模式**：执行普通应用代码。可运行在特权或非特权级别。
- **处理模式**：执行异常/中断处理程序（ISR）。始终为特权级别，使用 MSP。

通过 CONTROL 寄存器可切换栈指针和特权级别：

```c
// Switch thread mode to use PSP (process stack) and unprivileged
__set_CONTROL(__get_CONTROL() | CONTROL_SPSEL_Msk | CONTROL_nPRIV_Msk);
__ISB();  // Instruction Synchronization Barrier after CONTROL change

// Switch back to privileged + MSP (must be done from Handler mode via SVC)
// In thread mode unprivileged, cannot raise privilege directly
```

RTOS 通常让每个任务使用 PSP（独立栈），ISR 使用 MSP（共享栈），这样中断不会破坏任务栈，任务栈溢出也不会影响中断。

### 1.4 栈指针：MSP 与 PSP

Cortex-M 有两个栈指针：

- **MSP（Main Stack Pointer）**：复位后默认使用，处理模式（ISR）始终使用 MSP。
- **PSP（Process Stack Pointer）**：线程模式下可通过 CONTROL.SPSEL 选择使用。

应用场景：

- **裸机程序**：全程使用 MSP 即可，简单可靠。
- **RTOS 程序**：任务用 PSP（每任务独立栈），ISR 用 MSP（共享）。这样 ISR 栈与任务栈分离，可大幅节省 RAM（不需要每个任务都预留 ISR 栈空间）。

```c
// Inspect current stack pointer usage
uint32_t control = __get_CONTROL();
uint32_t used_sp = (control & CONTROL_SPSEL_Msk) ? __get_PSP() : __get_MSP();
printf("Active SP: %s, value=0x%08X\n",
       (control & CONTROL_SPSEL_Msk) ? "PSP" : "MSP", used_sp);
```

### 1.5 异常进入与返回流程

异常进入（Entry）流程由硬件自动完成：

1. **压栈（Stacking）**：硬件自动把 R0-R3、R12、LR、PC、xPSR 共 8 个寄存器压入当前栈（MSP 或 PSP）。栈帧结构如下：

```
偏移   寄存器
0x00   xPSR
0x04   PC    (返回地址)
0x08   LR    (R14)
0x0C   R12
0x10   R3
0x14   R2
0x18   R1
0x1C   R0
```

若 FPU 使能且异常发生时在用 FP，额外压入 18 个浮点寄存器（S0-S15、FPSCR、对齐保留），栈帧扩展到 0x68 字节。

2. **取向量**：从向量表读取对应异常的入口地址。
3. **更新寄存器**：LR 被赋值为 EXC_RETURN（特殊值，告知硬件返回时如何出栈），PC 跳转到 ISR。
4. **切换模式**：进入处理模式，使用 MSP。

异常返回（Exit）流程：

1. ISR 执行完毕，执行 `BX LR`（LR = EXC_RETURN）。
2. **出栈（Unstacking）**：硬件自动恢复 8（或 26）个寄存器。
3. **恢复执行**：返回被中断的代码。

EXC_RETURN 的取值含义：

| EXC_RETURN | 含义 |
|------------|------|
| 0xFFFFFFF1 | 返回处理模式，使用 MSP（嵌套中断返回） |
| 0xFFFFFFF9 | 返回线程模式，使用 MSP |
| 0xFFFFFFFD | 返回线程模式，使用 PSP |
| 0xFFFFFFE1 | 返回处理模式，MSP，含 FPU 状态 |
| 0xFFFFFFE9 | 返回线程模式，MSP，含 FPU 状态 |
| 0xFFFFFFED | 返回线程模式，PSP，含 FPU 状态 |

理解 EXC_RETURN 对调试栈溢出、手工触发 PendSV 切换等场景非常关键。

### 1.6 与传统 ARM7TDMI 中断模型对比

Cortex-M 的中断模型相比早期 ARM7TDMI（IRQ/FIQ）有革命性改进：

| 特性 | ARM7TDMI | Cortex-M |
|------|----------|----------|
| 中断控制器 | 外部 VIC（如 PL190） | 内置 NVIC，紧耦合 |
| 入口方式 | 统一入口 + 软件分发 | 向量化，硬件直接跳转 |
| 压栈 | 软件压栈（ISR 开头） | 硬件自动压栈 |
| 延迟 | 24-42 周期 | 12-16 周期（M3/M4） |
| 优先级 | IRQ/FIQ 两级 | 8-256 级，可分组 |
| 嵌套 | 软件管理 | 硬件自动 |
| 指令集 | ARM/Thumb | Thumb-2 |

Cortex-M 让中断处理从"需要老练汇编"变成"用 C 直接写 ISR"，大幅降低开发门槛。

---

## 2. NVIC 嵌套向量中断控制器

NVIC（Nested Vectored Interrupt Controller）是 Cortex-M 内核紧耦合的中断控制器，负责管理外部中断的使能、挂起、优先级与激活状态。本章是本文档的核心。

### 2.1 NVIC 寄存器映射

NVIC 寄存器位于系统控制空间（SCS，地址 0xE000E100 起），按中断编号组织。每个中断有 3 类寄存器：使能（ISER/ICER）、挂起（ISPR/ICPR）、活跃（IABR），外加优先级寄存器（IP）。

| 寄存器 | 地址偏移 | 功能 |
|--------|---------|------|
| ISER0-7 | 0x100 | 中断使能置位（写 1 使能） |
| ICER0-7 | 0x180 | 中断使能清除（写 1 禁能） |
| ISPR0-7 | 0x200 | 中断挂起置位（写 1 挂起） |
| ICPR0-7 | 0x280 | 中断挂起清除（写 1 解除挂起） |
| IABR0-7 | 0x300 | 中断活跃位（只读，1=正在执行） |
| IP0-239 | 0x400 | 8 位优先级寄存器（每中断一字节） |
| STIR | 0xF00 | 软件触发中断寄存器 |

CMSIS 提供了封装函数，开发者无需直接操作地址：

```c
// Enable interrupt
NVIC_EnableIRQ(USART1_IRQn);
// Disable interrupt
NVIC_DisableIRQ(USART1_IRQn);
// Set pending (trigger software interrupt)
NVIC_SetPendingIRQ(USART1_IRQn);
// Clear pending
NVIC_ClearPendingIRQ(USART1_IRQn);
// Get active state (1 if ISR currently executing)
uint32_t active = NVIC_GetActive(USART1_IRQn);
// Set priority
NVIC_SetPriority(USART1_IRQn, 5);
// Get priority
uint8_t prio = NVIC_GetPriority(USART1_IRQn);
```

### 2.2 优先级分组：PRIGROUP、抢占优先级与子优先级

这是 Cortex-M 中断系统最易混淆也最关键的概念。**PRIGROUP** 寄存器（位于 SCB->AIRCR 的 bit[10:8]）将优先级位分成**抢占优先级（Preempting Priority）** 和**子优先级（Subpriority）** 两段。

- **抢占优先级**：决定一个中断能否抢占另一个正在执行的中断。数值越小抢占能力越强。
- **子优先级**：当两个中断**同时挂起**时，决定哪个先被服务。**子优先级不影响抢占**，只影响挂起时的排队顺序。

**关键规则**：
- 仅当新中断的抢占优先级数值**严格小于**（更高）当前活跃中断的抢占优先级时，才会发生抢占。
- 子优先级相同的两个中断不会互相抢占，按挂起顺序排队。

STM32 HAL 封装了分组设置：

```c
// Set priority group: 4 bits for preemption, 0 for subpriority
// This is the most common choice for STM32 (NVIC_PRIORITYGROUP_4)
HAL_NVIC_SetPriorityGrouping(NVIC_PRIORITYGROUP_4);

// The HAL mapping:
// NVIC_PRIORITYGROUP_0 -> 4 bits sub, 0 pre  (no preemption among IRQs)
// NVIC_PRIORITYGROUP_1 -> 3 bits sub, 1 pre  (2 preempt levels)
// NVIC_PRIORITYGROUP_2 -> 2 bits sub, 2 pre  (4 preempt levels)
// NVIC_PRIORITYGROUP_3 -> 1 bit sub,  3 pre  (8 preempt levels)
// NVIC_PRIORITYGROUP_4 -> 0 bits sub, 4 pre  (16 preempt levels, recommended)
```

直接操作 AIRCR 设置 PRIGROUP（CMSIS 底层）：

```c
// Direct AIRCR write to set PRIGROUP
void NVIC_SetPriorityGrouping_raw(uint32_t group) {
    uint32_t reg_value;
    uint32_t PriorityGroupTmp = (group & 0x07);  // Only 3 bits
    reg_value = SCB->AIRCR;
    // Clear key and PRIGROUP bits
    reg_value &= ~(SCB_AIRCR_VECTKEY_Msk | SCB_AIRCR_PRIGROUP_Msk);
    // Write key 0x5FA and new PRIGROUP
    reg_value = reg_value |
                (0x5FA << SCB_AIRCR_VECTKEY_Pos) |
                (PriorityGroupTmp << SCB_AIRCR_PRIGROUP_Pos);
    SCB->AIRCR = reg_value;
}
```

### 2.3 优先级分组完整对照表

设实现位数为 N（如 STM32F4 的 N=4），PRIGROUP 取值 0-7 对应的分组关系：

| PRIGROUP | 抢占优先级位数 | 子优先级位数 | 抢占级数 | 子优先级数 |
|----------|--------------|-------------|---------|-----------|
| 0 | N-0 | 0 | 2^N | 1 |
| 1 | N-1 | 1 | 2^(N-1) | 2 |
| 2 | N-2 | 2 | 2^(N-2) | 4 |
| 3 | N-3 | 3 | 2^(N-3) | 8 |
| 4 | N-4 | 4 | 2^(N-4) | 16 |
| 5 | N-5 | 5 | 2^(N-5) | 32 |
| 6 | N-6 | 6 | 2^(N-6) | 64 |
| 7 | N-7 | 7 | 2^(N-7) | 128 |

对 STM32F4（N=4）：

| PRIGROUP | 抢占位数 | 子优先级位数 | 抢占级数 | 子级数 | HAL 宏 |
|----------|--------|------------|---------|-------|--------|
| 3 | 1 | 3 | 2 | 8 | GROUP_0 |
| 4 | 2 | 2 | 4 | 4 | GROUP_1 |
| 5 | 3 | 1 | 8 | 2 | GROUP_2 |
| 6 | 4 | 0 | 16 | 1 | GROUP_3 |
| 7 | 4 | 0 | 16 | 1 | GROUP_4 |

注意：当 PRIGROUP >= 6 时（对 N=4），子优先级位数为 0，所有位都用于抢占优先级。这就是为什么 `NVIC_PRIORITYGROUP_4`（HAL）等价于 PRIGROUP=3（因为 HAL 宏名中的数字表示抢占位数，而非 PRIGROUP 值）。

### 2.4 抢占优先级与子优先级的实战选择

**推荐方案：NVIC_PRIORITYGROUP_4（全部 16 级抢占，无子优先级）**

理由：
1. 嵌入式实时系统真正需要的是抢占能力，子优先级只是挂起队列的 tie-breaker，作用有限。
2. 16 级抢占足以满足绝大多数应用，且语义清晰（数值小即抢占强）。
3. 子优先级容易引起误解，调试时不直观。

何时需要子优先级：
- 多个同类外设（如 5 个 UART）的挂起顺序很重要，且不想用抢占（怕嵌套开销），可设 GROUP_2（4 抢占 + 4 子优先级）。

```c
// Recommended setup at startup
void interrupt_system_init(void) {
    // Use 16 preempt levels, no subpriority
    HAL_NVIC_SetPriorityGrouping(NVIC_PRIORITYGROUP_4);
    // Assign priorities by criticality
    HAL_NVIC_SetPriority(SysTick_IRQn,    0, 0);  // Highest (kernel tick)
    HAL_NVIC_SetPriority(DMA1_Stream0_IRQn,1, 0); // DMA for high-throughput
    HAL_NVIC_SetPriority(USART1_IRQn,     2, 0);  // Comms
    HAL_NVIC_SetPriority(TIM2_IRQn,       3, 0);  // Timer
    HAL_NVIC_SetPriority(EXTI0_IRQn,      4, 0);  // Button
    // SysTick at 0 lets RTOS tick preempt everything for accurate scheduling
}
```

### 2.5 优先级数值"小即高"的陷阱

Cortex-M 优先级**数值越小优先级越高**，这与许多人的直觉相反（Unix nice 值也是这样，但 RTOS 任务优先级通常相反）。

- 0 是最高优先级（Reset/NMI 除外，它们是负数 -3/-2/-1，固定最高）。
- 0xFF 是最低优先级（对 4 位实现，写入 0xFF 会被截断为 0xF0，即 15）。

```c
// Common mistake: thinking higher number = higher priority
// WRONG: assigning "more important" interrupt a larger number
HAL_NVIC_SetPriority(USART1_IRQn, 15, 0);  // This is LOWEST, not highest!

// Correct: assign critical interrupts smaller numbers
HAL_NVIC_SetPriority(USART1_IRQn, 0, 0);   // Highest
HAL_NVIC_SetPriority(TIM2_IRQn,   5, 0);   // Medium
HAL_NVIC_SetPriority(EXTI0_IRQn,  10, 0);  // Lower
```

迁移优先级数值时（如从 8 位 MCU 迁移），务必翻转数值映射。

### 2.6 中断使能与挂起的"记忆"特性

NVIC 的挂起寄存器有"记忆"：即使中断被禁用（ICER 清除），挂起位仍会保留；一旦重新使能，挂起的中断会立即执行。这对去抖动设计很关键：

```c
// Disable UART interrupt during critical section
NVIC_DisableIRQ(USART1_IRQn);
// ... critical code ...
// If a byte arrived during disable, it's now pending
// Re-enable will immediately fire the ISR
NVIC_EnableIRQ(USART1_IRQn);
// To discard pending interrupt instead:
NVIC_ClearPendingIRQ(USART1_IRQn);
NVIC_EnableIRQ(USART1_IRQn);
```

挂起位是"置位优先"的：如果在禁用期间同一中断多次挂起，只记录一次（不计数）。若需要计数，必须用软件队列。

### 2.7 软件触发中断（STIR / PendSV）

通过写 STIR 寄存器或调用 `NVIC_SetPendingIRQ()` 可软件触发中断。常见用途：

- **延迟处理**：ISR 中标记工作，退出后由低优先级中断处理（避免长时间占用高优先级 ISR）。
- **任务切换**：RTOS 用 PendSV 触发上下文切换。
- **核间通信**：多核系统中触发另一个核的中断。

```c
// Software-trigger a custom interrupt (e.g., SOFTWARE_IRQ0 defined as IRQn)
// Method 1: CMSIS
NVIC_SetPendingIRQ(SOFTWARE_IRQ0_IRQn);

// Method 2: Direct STIR write (only works if not in Handler mode? actually works)
SCB->SCR |= SCB_SCR_SEVONPEND_Msk;  // Optional: wake on pending
*((volatile uint32_t *)0xE000EF00) = SOFTWARE_IRQ0_IRQn;
```

### 2.8 中断活跃状态与重入

IABR 寄存器反映中断是否正在执行（活跃位）。活跃位在 ISR 进入时由硬件置位，退出时清除。

- 同一中断**默认不可重入**：活跃期间再次挂起不会抢占自己（因为优先级相同）。
- 若需要重入，可在 ISR 中手动清除挂起位并降低自身优先级，但极其危险，不推荐。

```c
// Check if ISR is currently running (from main code)
if (NVIC_GetActive(DMA1_Stream0_IRQn)) {
    // DMA ISR is executing, do not interfere
}
```

### 2.9 NVIC 与外设中断的协作

NVIC 只负责"是否响应"和"何时响应"，外设中断标志（如 USART SR.TXE）由外设自身维护。典型流程：

1. 外设产生事件，置位外设状态寄存器（如 USART1->SR.TXE=1）。
2. 若外设中断使能（USART1->CR1.TXEIE=1），向 NVIC 发出中断请求。
3. NVIC 判断优先级后，挂起并最终激活 USART1_IRQn。
4. ISR 执行，**必须清除中断源**（读 DR 或写 SR），否则退出后立即重入。

```c
void USART1_IRQHandler(void) {
    if (USART1->SR & USART_SR_RXNE) {
        // Read DR clears RXNE flag
        uint8_t data = USART1->DR;
        ring_buffer_put(&rx_ring, data);
    }
    if (USART1->SR & USART_SR_TXE) {
        if (ring_buffer_get(&tx_ring, &data)) {
            USART1->DR = data;  // Write DR clears TXE
        } else {
            // No more data, disable TXE interrupt to stop firing
            USART1->CR1 &= ~USART_CR1_TXEIE;
        }
    }
    // If flags not cleared here, ISR will re-fire infinitely
}
```

### 2.10 中断屏蔽：PRIMASK / FAULTMASK / BASEPRI

除了 NVIC 的逐中断使能，Cortex-M 提供三个全局屏蔽寄存器，用于临界区保护：

| 寄存器 | 作用 | 影响 NMI/HardFault |
|--------|------|-------------------|
| PRIMASK | 屏蔽所有可屏蔽异常（IRQ） | 不影响 NMI/HardFault |
| FAULTMASK | 屏蔽所有异常（含 HardFault，除 NMI） | 影响 HardFault |
| BASEPRI | 屏蔽优先级数值 ≥ BASEPRI 的中断 | 不影响更高优先级 |

```c
// Critical section: disable all IRQs (PRIMASK)
void critical_section_primask(void) {
    __disable_irq();      // Set PRIMASK=1
    // ... atomic operations ...
    __enable_irq();       // Clear PRIMASK
}

// Critical section with BASEPRI (preferred in RTOS)
// Disables only interrupts with priority >= 5, keeping high-priority IRQs (0-4) alive
void critical_section_basepri(void) {
    uint32_t basepri_save = __get_BASEPRI();
    __set_BASEPRI(5 << (8 - __NVIC_PRIO_BITS));  // 5 in priority field
    // ... atomic operations ...
    __set_BASEPRI(basepri_save);
}

// FAULTMASK: even HardFault disabled (use with extreme caution)
__set_FAULTMASK(1);
// ... must re-enable quickly ...
__set_FAULTMASK(0);
```

FreeRTOS 使用 BASEPRI 实现临界区，这样高优先级中断（如电机控制）不被 RTOS 临界区阻塞，保证实时性。

---

## 3. 中断延迟与尾链、迟来机制

中断延迟（Interrupt Latency）是实时系统的核心指标。Cortex-M3/M4 凭借硬件压栈和向量化机制，把延迟压缩到 **12 个时钟周期**，本章详解其构成与优化。

### 3.1 中断延迟定义

中断延迟有几种定义，需明确区分：

- **硬件延迟**：从中断请求到 ISR 第一条指令执行所经历的周期数。Cortex-M3/M4 为 **12 cycles**（无 FPU 状态保存时）。
- **响应延迟**：从外设事件发生到应用代码实际处理该事件的时间。包含硬件延迟 + ISR 入口开销 + 调度延迟（RTOS）。
- **抖动（Jitter）**：同一中断多次响应延迟的变化范围。硬实时系统要求抖动有上界。

本文聚焦硬件延迟，即 **12 cycles** 这一关键数字。

### 3.2 12 周期延迟的构成

Cortex-M3/M4 的中断进入流程在硬件上分为以下阶段，合计 **12 个周期**：

| 周期数 | 阶段 | 说明 |
|--------|------|------|
| 1 | 请求采样 | 在指令边界采样中断请求 |
| 2-9 | 压栈（8 寄存器） | 硬件自动压入 R0-R3/R12/LR/PC/xPSR，8 个 store 操作与总线时序相关 |
| 8-10 | 取向量 | 从向量表读取 ISR 入口地址（与压栈部分并行） |
| 11-12 | 取 ISR 指令 | 取 ISR 第一条指令并跳转 |

实际测量 12 cycles 是在零等待内存（紧耦合 SRAM）下的最佳值。影响因素：

- **Flash 等待周期**：从 Flash 取向量需等待周期（如 168MHz STM32F4 需 5 wait states），增加 3-5 周期。
- **缓存命中**：ART Accelerator 缓存命中可抵消 Flash 等待。
- **总线竞争**：DMA 与 CPU 同时访问总线时延迟。
- **FPU 状态保存**：若 ISR 用到 FPU 且需要保存 FP 上下文，增加约 16 周期。
- **指令不可中断**：LDM/STM 多寄存器加载存储指令需执行完才能响应。

### 3.3 实测中断延迟

用 GPIO 翻转 + 示波器/逻辑分析仪实测延迟：

```c
// Configure a GPIO as output for timing probe
// Configure an external interrupt (EXTI) on another pin
// Measure: apply rising edge to EXTI pin, observe delay to GPIO toggle in ISR

volatile uint32_t irq_entry_tick;

void EXTI0_IRQHandler(void) {
    // Toggle probe GPIO immediately (first instruction ideally)
    GPIOA->BSRR = GPIO_PIN_0;  // Set PA0 high
    // ... actual ISR work ...
    // Clear EXTI pending flag
    EXTI->PR = EXTI_LINE_0;
    GPIOA->BSRR = (GPIO_PIN_0 << 16);  // Set PA0 low
}

// On scope: measure time from EXTI trigger edge to PA0 rising edge
// At 84MHz: 12 cycles = 12/84MHz = ~143 ns
// Typical measured on STM32F4 (Flash w/ART): 18-25 cycles (~210-300ns)
```

DWT（Data Watchpoint and Trace）周期计数器可精确测量：

```c
// Use DWT CYCCNT for cycle-accurate latency measurement
void EXTI0_IRQHandler(void) {
    uint32_t cyc = DWT->CYCCNT;  // Read cycle counter (first thing)
    // Compare to a timestamp captured at trigger
    last_latency_cycles = cyc - trigger_cyc;
    // ... handle interrupt ...
    EXTI->PR = EXTI_LINE_0;
}

// Setup DWT
void dwt_init(void) {
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
}
```

### 3.4 尾链（Tail-Chaining）机制

**尾链** 是 Cortex-M3/M4 最重要的中断优化机制之一。当一个 ISR 执行完毕时，若 NVIC 中还有挂起的中断，硬件不会先出栈再入栈，而是直接跳转到下一个挂起中断的 ISR，省去 8 寄存器的出栈+入栈（共 16 次内存访问）。

**无尾链** 的流程：
```
ISR1 执行完 → 出栈（8 寄存器）→ 取向量 → 压栈（8 寄存器）→ ISR2 执行
延迟：8 + 2 + 8 = ~18 cycles
```

**有尾链** 的流程：
```
ISR1 执行完 → 取向量 → ISR2 执行（复用已压栈的栈帧）
延迟：6 cycles
```

节省约 12 个周期。这在多个中断密集发生时（如 DMA 完成后立即 UART 发送）效果显著。

```c
// Example: DMA complete ISR triggers UART processing
void DMA1_Stream0_IRQHandler(void) {
    if (DMA1->LISR & DMA_LISR_TCIF0) {
        DMA1->LIFCR = DMA_LIFCR_CTCIF0;  // Clear flag
        // Signal UART to start sending (via pending UART ISR)
        NVIC_SetPendingIRQ(USART1_IRQn);  // Tail-chained next
    }
}
// USART1_IRQHandler will execute immediately after DMA ISR via tail-chain
void USART1_IRQHandler(void) {
    // Process DMA-prepared buffer
}
```

### 3.5 迟来（Late-Arrival）机制

**迟来** 处理另一种优化场景：当一个低优先级中断正在**压栈阶段**（尚未开始执行 ISR）时，一个更高优先级的中断到达。硬件会立即切换到高优先级中断，避免先服务低优先级再抢占的浪费。

**无迟来优化** 的流程：
```
低优先级 IRQ 到达 → 压栈 → 取低优先级向量 → 高优先级 IRQ 到达
→ 取消压栈？不，继续低优先级 → 执行低优先级 ISR 一条指令 → 抢占
→ 压栈（高优先级）→ 执行高优先级 ISR → 出栈 → 继续低优先级
```

**有迟来优化** 的流程：
```
低优先级 IRQ 到达 → 压栈中 → 高优先级 IRQ 到达
→ 取消低优先级向量获取，改取高优先级向量
→ 执行高优先级 ISR → 出栈后执行低优先级 ISR（尾链）
```

迟来优化节省了一次压栈+出栈（约 16 周期），且保证了高优先级中断的最快响应。

条件：高优先级中断必须在低优先级中断的压栈窗口（约 8 周期）内到达才能触发迟来。

### 3.6 三种优化机制对比

| 机制 | 触发场景 | 节省周期 | 适用情况 |
|------|---------|---------|---------|
| 尾链 | ISR 结束时有挂起中断 | ~12 | 连续中断流 |
| 迟来 | 压栈期间更高优先级到达 | ~12 | 突发高优先级 |
| 压栈出栈合并 | 嵌套中断退出 | ~8 | 多级嵌套 |

Cortex-M0/M0+ **不支持迟来优化**，且尾链能力有限，因此 M0 的中断延迟和密集场景表现弱于 M3/M4。

### 3.7 影响延迟的代码因素

1. **多周期指令**：LDM/STM、LDRD/STRD、除法指令（SDIV/UDIV，2-12 周期）不可中断。对延迟敏感时避免在临界路径用除法。

```c
// BAD: division in timing-critical path
uint32_t scaled = value / 1000;  // SDIV blocks interrupt up to 12 cycles

// GOOD: multiply by reciprocal (precomputed)
uint32_t scaled = (uint32_t)((uint64_t)value * 0x10624DD3 >> 38);  // /1000
```

2. **睡眠模式**：WFI（Wait For Interrupt）后唤醒需额外 6+ 周期恢复时钟。低功耗与低延迟有取舍。

3. **Flash 等待**：把延迟关键 ISR 放到 SRAM 执行（`__RAM_FUNC`）可消除 Flash 等待。

```c
// Place ISR in SRAM for zero-wait-state execution
__RAM_FUNC void TIM2_IRQHandler(void) {
    // This ISR runs from SRAM, no Flash wait states
    TIM2->SR = ~TIM_SR_UIF;  // Clear update flag
    // ... high-speed control loop ...
}
// Note: function must be copied to SRAM at startup (scatter loading)
```

4. **FPU 上下文**：若 ISR 使用浮点，且任务也用浮点，硬件会自动保存 FP 上下文（额外 ~16 周期）。可用 `__attribute__((pcs("naked")))` 或在 ISR 入口禁用 FPU。

### 3.8 中断延迟测量基准

下表为各 Cortex-M 型号在零等待内存下的典型中断延迟：

| 型号 | 最小延迟（cycles） | 含 FPU | 备注 |
|------|------------------|--------|------|
| Cortex-M0 | 15 | N/A | 无尾链优化 |
| Cortex-M0+ | 15 | N/A | 同 M0 |
| Cortex-M3 | 12 | N/A | 有尾链/迟来 |
| Cortex-M4 | 12 | 12（无 FP 保存）/ 28（有） | FPU 自动保存 |
| Cortex-M7 | 12 | 12/28 | 双发射，缓存命中更快 |
| Cortex-M33 | 12 | 12/28 | v8-M，含安全扩展 |

实测值因 Flash 等待、缓存、总线负载而高于理论值。设计硬实时系统时应留 2x 余量。

### 3.9 降低延迟的设计准则

1. **缩短 ISR**：ISR 只做"清除标志 + 标记事件"，繁重处理交给主循环或低优先级任务。
2. **合理设优先级**：延迟敏感中断（电机、通信）给高优先级（数值小），普通中断给低优先级。
3. **避免在 ISR 中调用阻塞 API**：FreeRTOS 的 `FromISR` API 都是非阻塞的。
4. **用 DMA 替代中断搬运**：每个字节一次中断的 UART 在 1Mbps 下每毫秒触发 100 次 ISR，改用 DMA 几乎零开销。
5. **把 ISR 放 SRAM**：消除 Flash 等待，关键控制环路推荐。
6. **减少临界区长度**：BASEPRI 临界区要短，避免屏蔽高优先级中断过久。

```c
// Good ISR pattern: minimal work, defer heavy processing
void USART1_IRQHandler(void) {
    BaseType_t higher_priority_task_woken = pdFALSE;
    if (USART1->SR & USART_SR_RXNE) {
        uint8_t byte = USART1->DR;  // Clear flag by reading
        // Just send to queue, do NOT process here
        xQueueSendFromISR(rx_queue, &byte, &higher_priority_task_woken);
    }
    // Yield to high-priority task if woken
    portYIELD_FROM_ISR(higher_priority_task_woken);
}
```

---

## 4. 异常类型详解

Cortex-M 定义了系统异常（编号 1-15）和外部中断（编号 16+）共两类。每类异常的触发条件、优先级、可配置性不同，本章逐一详解。

### 4.1 系统异常总览

| 编号 | 异常 | 优先级 | 使能 | 触发条件 |
|------|------|--------|------|---------|
| 1 | Reset | -3（固定最高） | 永远 | 上电/复位 |
| 2 | NMI | -2（固定） | 永远 | NMI 引脚或看门狗/时钟故障 |
| 3 | HardFault | -1（固定） | 永远 | 不可恢复的硬件错误，或可配置 fault 升级 |
| 4 | MemManage | 可编程 | 可禁用 | MPU 违规、不可执行地址执行 |
| 5 | BusFault | 可编程 | 可禁用 | 总线错误（预取/数据访问失败） |
| 6 | UsageFault | 可编程 | 可禁用 | 未定义指令、除零、未对齐访问 |
| 7-10 | 保留 | — | — | — |
| 11 | SVCall | 可编程 | 永远 | 执行 SVC 指令 |
| 12 | Debug Monitor | 可编程 | 可禁用 | 调试事件（断点/观察点） |
| 13 | 保留 | — | — | — |
| 14 | PendSV | 可编程 | 永远 | 写 ICSR.PENDSVSET 触发 |
| 15 | SysTick | 可编程 | 可禁用 | SysTick 定时器计数到 0 |
| 16+ | 外部中断 IRQn | 可编程 | 可禁用 | 外设中断请求 |

负优先级（-3/-2/-1）意味着这些异常**永远高于任何可编程中断**，无法被屏蔽（NMI 也不受 PRIMASK 影响）。

### 4.2 Reset 异常

Reset 是最特殊的异常：它不是"打断"程序，而是程序的起点。复位后：

1. 从向量表第一个字（地址 0x00000000 或 0x08000000）读取 MSP 初值。
2. 从第二个字读取 Reset_Handler 地址。
3. 跳转到 Reset_Handler，此时处于线程模式 + 特权 + 使用 MSP。

```c
// Typical Reset_Handler (in startup_stm32f407xx.s)
Reset_Handler:
    LDR     R0, =SystemInit        // Call system init (clock setup)
    BLX     R0
    LDR     R0, =__main            // Call C runtime init + main
    BX      R0
```

向量表起始（链接脚本定义）：

```c
// Vector table first entries
__attribute__((section(".isr_vector")))
void (* const g_pfnVectors[])(void) = {
    (void (*)(void))0x20020000,  // Initial MSP (stack top)
    Reset_Handler,               // Reset
    NMI_Handler,                 // NMI
    HardFault_Handler,           // HardFault
    MemManage_Handler,           // MemManage
    BusFault_Handler,            // BusFault
    UsageFault_Handler,          // UsageFault
    0, 0, 0, 0,                  // Reserved
    SVC_Handler,                 // SVCall
    DebugMon_Handler,            // Debug Monitor
    0,                           // Reserved
    PendSV_Handler,              // PendSV
    SysTick_Handler,             // SysTick
    // External interrupts follow...
    WWDG_IRQHandler,
    PVD_IRQHandler,
    // ...
};
```

### 4.3 NMI（不可屏蔽中断）

NMI 优先级 -2，仅次于 Reset，**不能被任何屏蔽寄存器关闭**（PRIMASK/FAULTMASK/BASEPRI 均无效）。NMI 触发源由芯片厂商定义：

- STM32：NMI 引脚（PC14）、看门狗（WWDG）、时钟安全系统（CSS）。
- Kinetis：看门狗、低压检测。

NMI 用于"必须处理"的灾难性事件。NMI 处理程序必须简短且不能依赖可能损坏的系统状态：

```c
void NMI_Handler(void) {
    // Check source: clock security system
    if (RCC->CIR & RCC_CIR_CSSF) {
        // HSE clock failed, switch to HSI
        RCC->CR |= RCC_CR_HSION;
        while (!(RCC->CR & RCC_CR_HSIRDY)) {}
        // Clear flag
        RCC->CIR |= RCC_CIR_CSSC;
    }
    // Log and optionally reset
    log_fault("NMI: clock failure");
}
```

### 4.4 HardFault 详解

HardFault 是最常遇到的故障异常。它有两类来源：

1. **硬件错误升级**：BusFault/MemManage/UsageFault 被禁用时，对应错误升级为 HardFault。
2. **不可恢复错误**：栈损坏、向量表读取失败、ISR 地址无效等。

HardFault 处理程序通常用于诊断和系统恢复：

```c
// Detailed HardFault handler with register dump
__attribute__((naked)) void HardFault_Handler(void) {
    __asm volatile(
        "tst lr, #4                \n"  // Check EXC_RETURN bit 2
        "ite eq                    \n"
        "mrseq r0, msp             \n"  // Use MSP if 0
        "mrsne r0, psp             \n"  // Use PSP if 1
        "b HardFault_Handler_C     \n"  // Call C handler with stack frame
    );
}

void HardFault_Handler_C(uint32_t *stack_frame) {
    // stack_frame points to saved registers: R0,R1,R2,R3,R12,LR,PC,xPSR
    uint32_t r0  = stack_frame[0];
    uint32_t r1  = stack_frame[1];
    uint32_t r2  = stack_frame[2];
    uint32_t r3  = stack_frame[3];
    uint32_t r12 = stack_frame[4];
    uint32_t lr  = stack_frame[5];  // Link register at fault
    uint32_t pc  = stack_frame[6];  // Program counter at fault
    uint32_t psr = stack_frame[7];  // xPSR

    // Read fault status registers
    uint32_t cfsr = SCB->CFSR;
    uint32_t hfsr = SCB->HFSR;
    uint32_t mmfar = SCB->MMFAR;
    uint32_t bfar = SCB->BFAR;

    log_fault("HardFault: PC=0x%08X LR=0x%08X PSR=0x%08X", pc, lr, psr);
    log_fault("CFSR=0x%08X HFSR=0x%08X", cfsr, hfsr);
    log_fault("MMFAR=0x%08X BFAR=0x%08X", mmfar, bfar);
    log_fault("R0=0x%08X R1=0x%08X R2=0x%08X R3=0x%08X R12=0x%08X",
              r0, r1, r2, r3, r12);

    // Optional: trigger reset via watchdog
    // NVIC_SystemReset();

    while (1) {}  // Halt for debugging
}
```

CFSR（Configurable Fault Status Register）由三部分组成：

| 字段 | 地址 | 含义 |
|------|------|------|
| MMFSR | CFSR[7:0] | MemManage Fault Status |
| BFSR | CFSR[15:8] | BusFault Status |
| UFSR | CFSR[31:16] | UsageFault Status |

常用位：
- MMFSR.IACCVIOL（bit 0）：指令访问违规（XN 区域执行）。
- BFSR.PRECISERR（bit 1）：精确总线错误，BFAR 有效。
- BFSR.IMPRECISERR（bit 2）：非精确总线错误，BFAR 无效。
- UFSR.DIVBYZERO（bit 9）：除零。
- UFSR.UNDEFINSTR（bit 16）：未定义指令。

### 4.5 MemManage、BusFault、UsageFault

这三个可配置 fault 默认禁用，需在 SHCSR（System Handler Control and State Register）中使能：

```c
// Enable configurable faults
void enable_configurable_faults(void) {
    SCB->SHCSR |= SCB_SHCSR_MEMFAULTENA_Msk |    // MemManage
                  SCB_SHCSR_BUSFAULTENA_Msk |    // BusFault
                  SCB_SHCSR_USGFAULTENA_Msk;     // UsageFault
    // Optional: enable divide-by-zero trap
    SCB->CCR |= SCB_CCR_DIV_0_TRP_Msk;
    // Optional: enable unaligned access trap
    SCB->CCR |= SCB_CCR_UNALIGN_TRP_Msk;
}
```

使能后，对应错误会触发独立的 fault handler 而非升级到 HardFault，便于精确定位：

```c
void BusFault_Handler(void) {
    uint8_t bfsr = (SCB->CFSR >> 8) & 0xFF;
    if (bfsr & (1 << 1)) {  // PRECISERR
        log_fault("Precise BusFault at 0x%08X", SCB->BFAR);
    } else if (bfsr & (1 << 2)) {  // IMPRECISERR
        log_fault("Imprecise BusFault (no BFAR)");
    }
    SCB->CFSR |= (0xFF << 8);  // Clear BFSR
    // Decide: continue or halt
}
```

### 4.6 SVCall 与 PendSV

**SVCall**（SuperVisor Call）由 `SVC #n` 指令触发，是用户态请求内核服务的标准机制。FreeRTOS 用 SVC 启动调度器：

```c
// SVC handler reads the immediate number
void SVC_Handler(void) {
    uint32_t *stack;
    __asm volatile("tst lr, #4\nite eq\nmrseq %0, msp\nmrsne %0, psp" : "=r"(stack));
    uint8_t svc_number = ((uint8_t *)stack[6])[-2];  // PC-2 is SVC instruction
    switch (svc_number) {
        case 0:  // SVC_0: start scheduler
            vPortStartFirstTask();
            break;
        case 1:  // SVC_1: yield
            // ...
            break;
    }
}
```

**PendSV**（Pendable SerVice）是可挂起的系统调用，优先级通常设为最低（0xFF）。它的"可挂起 + 低优先级"特性使其成为 RTOS 上下文切换的最佳载体：

```c
// PendSV handler for FreeRTOS context switch (simplified)
__attribute__((naked)) void PendSV_Handler(void) {
    __asm volatile(
        "mrs r0, psp               \n"
        "ldr r3, =pxCurrentTCB     \n"
        "ldr r2, [r3]              \n"  // Current TCB
        // Save R4-R11 to PSP stack
        "stmdb r0!, {r4-r11}       \n"
        "str r0, [r2]              \n"  // Save new stack top
        "push {r3, r14}            \n"
        "bl vTaskSwitchContext      \n"  // Select next task
        "pop {r3, r14}             \n"
        "ldr r1, [r3]              \n"  // Next TCB
        "ldr r0, [r1]              \n"  // Next stack top
        "ldmia r0!, {r4-r11}       \n"  // Restore R4-R11
        "msr psp, r0               \n"
        "bx r14                    \n"  // Return
    );
}
```

### 4.7 SysTick 异常

SysTick 是内核内置的 24 位向下计数定时器，专为 RTOS 心跳设计：

```c
// Configure SysTick for 1ms tick at 168MHz
SysTick_Config(SystemCoreClock / 1000);
// This sets LOAD = 168000-1, CTRL = clksource | tickint | enable

void SysTick_Handler(void) {
    // HAL_IncTick increments uwTick for HAL_Delay
    HAL_IncTick();
    // FreeRTOS tick (if using SysTick as RTOS tick source)
    #if USE_FreeRTOS
    xPortSysTickHandler();
    #endif
}
```

SysTick 的优势：内核内建、时钟源可选（HCLK 或 HCLK/8）、所有 Cortex-M 都有，保证 RTOS 可移植性。

---

## 5. 中断向量表与重定位

### 5.1 向量表结构

向量表是异常入口地址的数组，位于地址空间某处（默认 0x00000000 或 0x08000000）。每项 4 字节（一个字），顺序按异常编号：

| 偏移 | 内容 |
|------|------|
| 0x00 | MSP 初值（不是函数地址，是栈顶地址） |
| 0x04 | Reset_Handler |
| 0x08 | NMI_Handler |
| 0x0C | HardFault_Handler |
| 0x10 | MemManage_Handler |
| 0x14 | BusFault_Handler |
| 0x18 | UsageFault_Handler |
| ... | ... |
| 0x3C | SysTick_Handler |
| 0x40 | IRQ0 (WWDG) |
| 0x44 | IRQ1 |
| ... | ... |

向量表大小 = (16 + IRQ 数) × 4 字节。STM32F4 有 82 个 IRQ，向量表 98×4 = 392 字节。

### 5.2 向量表重定位（VTOR）

通过 SCB->VTOR 寄存器可在运行时改变向量表位置。常见用途：

1. **Bootloader + App 架构**：Bootloader 在 0x08000000，App 在 0x08008000，各自有向量表。
2. **动态修改 ISR**：把向量表复制到 SRAM，运行时改写某项实现"热替换"ISR。
3. **运行时固件升级**：升级后跳转到新固件，需重设 VTOR。

```c
// Relocate vector table to application
#define APP_VECTOR_TABLE_ADDR  0x08008000

void jump_to_application(void) {
    // Disable all interrupts before jump
    __disable_irq();
    // Clear all pending interrupts
    for (int i = 0; i < 8; i++) {
        NVIC->ICER[i] = 0xFFFFFFFF;
        NVIC->ICPR[i] = 0xFFFFFFFF;
    }
    // Set VTOR to app vector table
    SCB->VTOR = APP_VECTOR_TABLE_ADDR;
    __DSB();
    __ISB();
    // Read new MSP and Reset_Handler
    uint32_t app_msp = *(volatile uint32_t *)APP_VECTOR_TABLE_ADDR;
    uint32_t app_reset = *(volatile uint32_t *)(APP_VECTOR_TABLE_ADDR + 4);
    // Set MSP
    __set_MSP(app_msp);
    // Enable interrupts
    __enable_irq();
    // Jump to app reset handler
    ((void (*)(void))app_reset)();
}
```

### 5.3 运行时动态 ISR 替换

把向量表复制到 SRAM，即可在运行时替换特定 ISR：

```c
#define VECTAB_SIZE  (16 + 82)  // STM32F4: 16 system + 82 external
extern void (* const g_pfnVectors[])(void);  // Original in Flash

// SRAM copy of vector table
__attribute__((aligned(256)))
static void (*sram_vectors[VECTAB_SIZE])(void);

void relocate_vectors_to_sram(void) {
    // Copy original vectors to SRAM
    for (int i = 0; i < VECTAB_SIZE; i++) {
        sram_vectors[i] = g_pfnVectors[i];
    }
    // Point VTOR to SRAM copy (must be 256-byte aligned on M3, less strict on M4)
    __disable_irq();
    SCB->VTOR = (uint32_t)sram_vectors;
    __DSB();
    __ISB();
    __enable_irq();
}

// Now you can hot-swap an ISR
void replace_isr(IRQn_Type irqn, void (*new_handler)(void)) {
    sram_vectors[16 + irqn] = new_handler;
    __DSB();
    __ISB();
}
```

注意 VTOR 对齐要求：Cortex-M3 要求 256 字节对齐，M4/M7 要求 128 字节对齐（取决于实现位数）。

### 5.4 Bootloader 向量表设计

专业 bootloader 需正确处理向量表：

```c
// Bootloader main: stays at 0x08000000
int main(void) {
    HAL_Init();
    SystemClock_Config();
    // Check for upgrade request (button held, or command from host)
    if (upgrade_requested()) {
        run_firmware_updater();
    }
    // Default: jump to application at 0x08008000
    jump_to_application();
    while (1) {}  // Should never reach
}

// Application startup must NOT re-init VTOR if bootloader already did,
// OR must set it correctly. Recommended: app sets VTOR in SystemInit.
void SystemInit(void) {
    // Set VTOR to app's own vector table
    SCB->VTOR = 0x08008000;
    // ... rest of init ...
}
```

### 5.5 向量表与链接脚本

向量表位置由链接脚本控制。GCC 示例：

```
/* Linker script excerpt */
MEMORY {
    FLASH (rx)  : ORIGIN = 0x08000000, LENGTH = 1024K
    RAM   (rwx) : ORIGIN = 0x20000000, LENGTH = 192K
}

SECTIONS {
    .isr_vector : {
        KEEP(*(.isr_vector))  /* Vector table at very start of Flash */
    } > FLASH

    .text : {
        *(.text*)
        *(.rodata*)
    } > FLASH

    /* For RAM-based ISRs */
    .ramfunc : AT (_etext) {
        _sramfunc = .;
        *(.ramfunc*)
        _eramfunc = .;
    } > RAM
}
```

---

## 6. 优先级设计与分组策略

### 6.1 优先级设计原则

合理的优先级分配是实时系统设计的核心。原则：

1. **关键性优先**：越关键（错过会导致系统故障）的中断优先级数值越小。
2. **频率与时长权衡**：高频短中断可设高优先级；长耗时中断设低优先级避免阻塞其他。
3. **避免优先级反转**：共享资源时用互斥量优先级继承。
4. **预留高优先级**：0-2 级留给系统关键（SysTick、故障、电机控制），应用中断从 3 起。

### 6.2 STM32 中断优先级分配模板

| 优先级 | 中断 | 理由 |
|--------|------|------|
| 0 | SysTick | RTOS 心跳，必须准时 |
| 0 | HardFault 等 | 故障最高 |
| 1 | TIM1（电机 PWM） | 电流环 10kHz，错过炸管 |
| 2 | DMA1 Stream0 | 高速 ADC 采样 |
| 3 | USART1（指令） | 通信协议时序 |
| 4 | SPI1 | 显示刷新 |
| 5 | ADC1 | 一般采集 |
| 6 | TIM3 | 编码器 |
| 7 | USART2 | 调试日志 |
| 8-10 | CAN/USB | 容忍延迟 |
| 11-15 | EXTI/按钮 | 人机交互，无实时要求 |

```c
void nvic_priorities_init(void) {
    HAL_NVIC_SetPriorityGrouping(NVIC_PRIORITYGROUP_4);
    HAL_NVIC_SetPriority(SysTick_IRQn,        0, 0);
    HAL_NVIC_SetPriority(TIM1_UP_TIM10_IRQn,  1, 0);
    HAL_NVIC_SetPriority(DMA1_Stream0_IRQn,   2, 0);
    HAL_NVIC_SetPriority(USART1_IRQn,         3, 0);
    HAL_NVIC_SetPriority(SPI1_IRQn,           4, 0);
    HAL_NVIC_SetPriority(ADC_IRQn,            5, 0);
    HAL_NVIC_SetPriority(TIM3_IRQn,           6, 0);
    HAL_NVIC_SetPriority(USART2_IRQn,         7, 0);
    HAL_NVIC_SetPriority(CAN1_TX_IRQn,        8, 0);
    HAL_NVIC_SetPriority(USB_IRQn,           10, 0);
    HAL_NVIC_SetPriority(EXTI0_IRQn,         12, 0);
    HAL_NVIC_SetPriority(EXTI15_10_IRQn,     13, 0);
}
```

### 6.3 分组策略选择指南

| 场景 | 推荐分组 | 理由 |
|------|---------|------|
| 通用 RTOS 应用 | GROUP_4（16抢占） | 最大灵活性 |
| 简单裸机 | GROUP_4 | 无需子优先级 |
| 多 UART 同优先级 | GROUP_2（4抢占+4子） | 用子优先级排 UART 顺序 |
| 极简（仅几个中断） | GROUP_4 | 充分 |
| 需严格无嵌套 | GROUP_0（0抢占） | 但失去抢占能力 |

### 6.4 优先级反转与解决方案

经典优先级反转：低优先级任务持有锁，高优先级任务等待锁，中等优先级任务抢占低优先级任务，导致高优先级被间接阻塞。

Cortex-M 本身不解决优先级反转，需 RTOS 提供优先级继承互斥量：

```c
// FreeRTOS priority-inheritance mutex
SemaphoreHandle_t xMutex = xSemaphoreCreateMutex();

void high_prio_task(void *pv) {
    xSemaphoreTake(xMutex, portMAX_DELAY);  // Blocks if low task holds it
    // If low task holds mutex, RTOS temporarily raises low task's priority
    // to high task's priority, preventing medium task from preempting
    access_shared_resource();
    xSemaphoreGive(xMutex);  // Priority restored
}

// NOTE: Binary semaphores do NOT do priority inheritance!
// Use xSemaphoreCreateMutex() for priority inheritance, not CreateBinary.
```

### 6.5 临界区与优先级天花板

对于硬实时系统，可用"优先级天花板"协议：每个资源预设一个天花板优先级（所有使用者的最高优先级），进入临界区时直接把屏蔽阈值设到天花板：

```c
// Priority ceiling protocol implementation
#define RESOURCE_X_CEILING  3  // Highest priority among users

void enter_resource_x(void) {
    // Mask all interrupts at ceiling level and below (numerically >= ceiling)
    uint32_t save = __get_BASEPRI();
    __set_BASEPRI(RESOURCE_X_CEILING << (8 - __NVIC_PRIO_BITS));
    // Now no user of resource X can preempt, avoiding deadlock
}

void exit_resource_x(void) {
    // Restore previous BASEPRI
    __set_BASEPRI(0);  // or saved value
}
```

### 6.6 中断优先级与 RTOS 配置

FreeRTOS 的 `configMAX_SYSCALL_INTERRUPT_PRIORITY` 决定哪些中断可调用 RTOS API：

```c
// FreeRTOSConfig.h
#define configPRIO_BITS         4                // STM32F4 uses 4 priority bits
#define configLIBRARY_LOWEST_INTERRUPT_PRIORITY 15
#define configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY 5  // IRQs 0-4 CANNOT call RTOS API

// Derived
#define configKERNEL_INTERRUPT_PRIORITY (configLIBRARY_LOWEST_INTERRUPT_PRIORITY << (8 - configPRIO_BITS))
#define configMAX_SYSCALL_INTERRUPT_PRIORITY (configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY << (8 - configPRIO_BITS))
```

含义：优先级数值 0-4 的中断**不能**调用 `xQueueSendFromISR` 等 API（它们绕过 BASEPRI 屏蔽，可能导致 RTOS 内核数据损坏）。优先级 5-15 的中断可安全调用。

设计：把真正关键的硬件中断（电机）放 0-4，纯软件触发或可延迟的中断放 5-15。

## 7. 中断嵌套与抢占机制

### 7.1 硬件自动嵌套

Cortex-M 的中断嵌套完全由硬件管理，无需软件干预。当高优先级中断（数值小）打断低优先级中断（数值大）时：

1. 硬件压栈当前 ISR 的寄存器。
2. LR 被设为 EXC_RETURN=0xFFFFFFF1（返回处理模式，MSP），表示返回到另一个 ISR 而非线程。
3. 执行高优先级 ISR。
4. 高优先级 ISR 返回时，硬件出栈，回到被抢占的低优先级 ISR。

```c
// Example: USART1 (prio 3) preempted by TIM1 (prio 1)
void TIM1_UP_TIM10_IRQHandler(void) {  // prio 1, preempts USART
    TIM1->SR = ~TIM_SR_UIF;
    // ... motor control loop ...
    // On return, hardware goes back to USART1 ISR
}

void USART1_IRQHandler(void) {  // prio 3
    // Mid-execution, TIM1 fires and preempts here
    // ... after TIM1 returns, continue here ...
    if (USART1->SR & USART_SR_RXNE) {
        uint8_t b = USART1->DR;
    }
}
```

### 7.2 嵌套深度与栈消耗

每级嵌套消耗 32 字节栈（8 寄存器 × 4 字节，无 FPU）。FPU 使能时每级 104 字节。

```c
// Calculate required ISR stack depth
// Assume max nesting = 4 levels, with FPU
// Per level: 104 bytes (FPU frame) + ISR local vars
// Safety margin: 2x
#define MAX_NESTING    4
#define FRAME_SIZE     104   // With FPU
#define ISR_LOCAL_MAX  128   // Estimate per ISR
#define ISR_STACK_SIZE (MAX_NESTING * (FRAME_SIZE + ISR_LOCAL_MAX) * 2)
// = 4 * (104 + 128) * 2 = 1856 bytes, round to 2048
```

RTOS 中，ISR 栈即 MSP 栈，所有任务共享。裸机中 ISR 与主程序共用 MSP，需预留足够空间。

### 7.3 嵌套引发的死锁

嵌套中断共享数据时可能死锁。例如 ISR_A 持有自旋锁，被 ISR_B 抢占，ISR_B 也请求该锁 → 死锁（ISR_A 无法运行释放锁）。

解决：确保锁的优先级顺序，或使用 BASEPRI 在持锁时屏蔽可能请求同锁的中断：

```c
// Spinlock with interrupt masking for ISR safety
typedef struct {
    volatile uint8_t locked;
    uint8_t ceiling_prio;  // Highest priority that may use this lock
} isr_lock_t;

void isr_lock_take(isr_lock_t *l) {
    // Mask interrupts at/above ceiling (numerically <= ceiling)
    uint32_t save = __get_BASEPRI();
    if (l->ceiling_prio > 0) {
        __set_BASEPRI(l->ceiling_prio << (8 - __NVIC_PRIO_BITS));
    }
    while (__LDREXB(&l->locked)) {}  // Spin (only same/lower prio could hold)
    __STREXB(1, &l->locked);
    l->saved_basepri = save;
}

void isr_lock_give(isr_lock_t *l) {
    l->locked = 0;
    __set_BASEPRI(l->saved_basepri);
}
```

### 7.4 中断嵌套调试技巧

跟踪嵌套层级：

```c
volatile uint32_t g_isr_nesting = 0;
volatile uint32_t g_max_nesting = 0;

// Wrap each ISR with nesting counter
void USART1_IRQHandler(void) {
    uint32_t n = __get_BASEPRI();
    g_isr_nesting++;
    if (g_isr_nesting > g_max_nesting) g_max_nesting = g_isr_nesting;
    // ... actual ISR ...
    g_isr_nesting--;
}
```

或用 ITM/SEGGER SystemView 实时记录中断进出时间戳，可视化嵌套关系。

### 7.5 主动触发抢占： PendSV 模式

若 ISR 中需要执行较耗时操作但不希望阻塞其他中断，可触发低优先级 PendSV 延后处理：

```c
// High-prio ISR: minimal work, defer heavy work to PendSV
void DMA1_Stream0_IRQHandler(void) {
    DMA1->LIFCR = DMA_LIFCR_CTCIF0;
    g_dma_done = 1;
    // Trigger PendSV (lowest priority) to process buffer
    SCB->ICSR |= SCB_ICSR_PENDSVSET_Msk;
}

// PendSV handler: runs at lowest priority, doesn't block high-prio IRQs
void PendSV_Handler(void) {
    if (g_dma_done) {
        g_dma_done = 0;
        process_dma_buffer();  // Heavy processing here
    }
}
```

---

## 8. FreeRTOS 与中断管理

### 8.1 FreeRTOS 中断模型

FreeRTOS 在 Cortex-M 上的中断管理要点：

1. **SysTick** 作为 RTOS tick 源，优先级最低（= configKERNEL_INTERRUPT_PRIORITY）。
2. **PendSV** 用于上下文切换，优先级最低。
3. **SVC** 用于启动调度器和系统调用。
4. 优先级数值 ≥ configMAX_SYSCALL_INTERRUPT_PRIORITY 的中断可调用 RTOS API，更高优先级（数值更小）的中断**禁止**调用 RTOS API。

```c
// FreeRTOS Cortex-M port priorities (in FreeRTOSConfig.h)
#define configCPU_CLOCK_HZ          168000000
#define configTICK_RATE_HZ          1000
#define configPRIO_BITS             4
#define configLIBRARY_LOWEST_INTERRUPT_PRIORITY         15
#define configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY   5

#define configKERNEL_INTERRUPT_PRIORITY \
        (configLIBRARY_LOWEST_INTERRUPT_PRIORITY << (8 - configPRIO_BITS))
#define configMAX_SYSCALL_INTERRUPT_PRIORITY \
        (configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY << (8 - configPRIO_BITS))
```

### 8.2 FromISR API 使用

中断中必须使用 `FromISR` 后缀的 API，且不能阻塞：

```c
// ISR-safe queue send
BaseType_t xHigherPriorityTaskWoken = pdFALSE;
xQueueSendFromISR(xQueue, &item, &xHigherPriorityTaskWoken);
// If a higher-priority task was unblocked, request context switch
portYIELD_FROM_ISR(xHigherPriorityTaskWoken);

// ISR-safe event group set
EventBits_t bits = xEventGroupSetBitsFromISR(xEventGroup, BIT_0,
                                             &xHigherPriorityTaskWoken);
portYIELD_FROM_ISR(xHigherPriorityTaskWoken);

// ISR-safe semaphore give
xSemaphoreGiveFromISR(xBinarySemaphore, &xHigherPriorityTaskWoken);
portYIELD_FROM_ISR(xHigherPriorityTaskWoken);
```

### 8.3 延迟中断处理（Deferred Interrupt Processing）

最佳实践：ISR 只做清除标志 + 通知任务，繁重处理在任务中执行。

```c
// UART RX complete: ISR signals task, task processes
static QueueHandle_t uart_rx_queue;

void uart_rx_init(void) {
    uart_rx_queue = xQueueCreate(64, sizeof(uint8_t));
    // Enable RXNE interrupt
    USART1->CR1 |= USART_CR1_RXNEIE;
    HAL_NVIC_SetPriority(USART1_IRQn, 6, 0);  // >= MAX_SYSCALL (5)
    HAL_NVIC_EnableIRQ(USART1_IRQn);
}

void USART1_IRQHandler(void) {
    BaseType_t hpw = pdFALSE;
    if (USART1->SR & USART_SR_RXNE) {
        uint8_t byte = USART1->DR;  // Clear flag
        xQueueSendFromISR(uart_rx_queue, &byte, &hpw);
    }
    portYIELD_FROM_ISR(hpw);
}

// Task processes received bytes
void uart_rx_task(void *pv) {
    uint8_t byte;
    for (;;) {
        if (xQueueReceive(uart_rx_queue, &byte, portMAX_DELAY) == pdTRUE) {
            process_byte(byte);  // Heavy processing here
        }
    }
}
```

### 8.4 临界区与任务切换

FreeRTOS 临界区用 BASEPRI 实现，不屏蔽高优先级中断：

```c
// FreeRTOS critical section (internal)
#define portDISABLE_INTERRUPTS() \
    __set_BASEPRI(configMAX_SYSCALL_INTERRUPT_PRIORITY)
#define portENABLE_INTERRUPTS() \
    __set_BASEPRI(0)

// Application-level critical section
taskENTER_CRITICAL();
// ... access shared data ...
taskEXIT_CRITICAL();

// From ISR, use:
UBaseType_t uxSaved = taskENTER_CRITICAL_FROM_ISR();
// ... access shared data ...
taskEXIT_CRITICAL_FROM_ISR(uxSaved);
```

注意：`taskENTER_CRITICAL` 会嵌套计数，可重入；`portDISABLE_INTERRUPTS` 不可重入。

### 8.5 vPortValidateInterruptPriority

FreeRTOS 的 `configASSERT` 会检查 ISR 是否调用 API 时优先级合法。开启后，若高优先级中断调用 FromISR API 会触发断言失败：

```c
// In FreeRTOSConfig.h
#define configASSERT(x) if(!(x)) { taskDISABLE_INTERRUPTS(); while(1); }

// This catches bugs like:
// HAL_NVIC_SetPriority(TIM1_IRQn, 1, 0);  // prio 1 < MAX_SYSCALL(5)
// void TIM1_IRQHandler(void) {
//     xQueueSendFromISR(...);  // ASSERT FAILS! prio 1 cannot call RTOS API
// }
```

### 8.6 FreeRTOS 与 HAL 中断的协作

STM32 HAL 的中断回调常与 FreeRTOS 冲突。推荐模式：HAL 中断回调里给任务发信号，不在回调里做实际工作：

```c
// DMA complete callback signals task
void HAL_SPI_RxCpltCallback(SPI_HandleTypeDef *hspi) {
    BaseType_t hpw = pdFALSE;
    if (hspi == &hspi1) {
        xTaskNotifyFromISR(spi_task_handle, SPI_RX_DONE, eSetBits, &hpw);
    }
    portYIELD_FROM_ISR(hpw);
}

// SPI task waits for notification then processes
void spi_task(void *pv) {
    uint32_t notify;
    for (;;) {
        xTaskNotifyWait(0, SPI_RX_DONE, &notify, portMAX_DELAY);
        if (notify & SPI_RX_DONE) {
            process_spi_buffer();  // Heavy work in task context
            start_next_spi_transfer();
        }
    }
}
```

---

## 9. 外设中断实战

### 9.1 GPIO 外部中断（EXTI）

```c
// Configure PA0 as EXTI rising edge interrupt (button)
void exti0_init(void) {
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_SYSCFG_CLK_ENABLE();

    GPIO_InitTypeDef gi = {0};
    gi.Pin = GPIO_PIN_0;
    gi.Mode = GPIO_MODE_IT_RISING;
    gi.Pull = GPIO_PULLDOWN;
    HAL_GPIO_Init(GPIOA, &gi);

    // Map PA0 to EXTI line 0
    SYSCFG->EXTICR[0] &= ~SYSCFG_EXTICR1_EXTI0;
    SYSCFG->EXTICR[0] |= SYSCFG_EXTICR1_EXTI0_PA;

    HAL_NVIC_SetPriority(EXTI0_IRQn, 12, 0);
    HAL_NVIC_EnableIRQ(EXTI0_IRQn);
}

void EXTI0_IRQHandler(void) {
    // Debounce: only process if last event > 20ms ago
    static uint32_t last_tick = 0;
    uint32_t now = HAL_GetTick();
    if (now - last_tick > 20) {
        // Handle button press
        button_pressed_event();
    }
    last_tick = now;
    // Clear pending flag (mandatory)
    EXTI->PR = EXTI_LINE_0;
}
```

### 9.2 UART 接收中断 + 环形缓冲区

```c
#define RX_BUF_SIZE 256
typedef struct {
    uint8_t buf[RX_BUF_SIZE];
    volatile uint16_t head, tail;
} ring_t;

static ring_t rx_ring;

void ring_put(ring_t *r, uint8_t b) {
    uint16_t next = (r->head + 1) % RX_BUF_SIZE;
    if (next != r->tail) {  // Not full
        r->buf[r->head] = b;
        r->head = next;
    }
}

int ring_get(ring_t *r, uint8_t *b) {
    if (r->head == r->tail) return 0;  // Empty
    *b = r->buf[r->tail];
    r->tail = (r->tail + 1) % RX_BUF_SIZE;
    return 1;
}

void USART1_IRQHandler(void) {
    if (USART1->SR & USART_SR_RXNE) {
        uint8_t b = USART1->DR;  // Read clears RXNE
        ring_put(&rx_ring, b);
        // Optional: signal task on frame end (e.g., '\n')
    }
    // Overrun error handling
    if (USART1->SR & USART_SR_ORE) {
        (void)USART1->DR;  // Clear ORE by reading DR then SR
    }
}
```

### 9.3 定时器中断（PWM + 编码器）

```c
// TIM1 update interrupt for 10kHz control loop
void tim1_init(void) {
    __HAL_RCC_TIM1_CLK_ENABLE();
    TIM1->PSC = 0;            // No prescaler, 168MHz
    TIM1->ARR = 16800 - 1;    // 168MHz/16800 = 10kHz
    TIM1->DIER |= TIM_DIER_UIE;  // Enable update interrupt
    TIM1->CR1 |= TIM_CR1_CEN;
    HAL_NVIC_SetPriority(TIM1_UP_TIM10_IRQn, 1, 0);  // High priority
    HAL_NVIC_EnableIRQ(TIM1_UP_TIM10_IRQn);
}

void TIM1_UP_TIM10_IRQHandler(void) {
    if (TIM1->SR & TIM_SR_UIF) {
        TIM1->SR = ~TIM_SR_UIF;  // Clear flag FIRST
        // Read encoder
        int16_t enc = (int16_t)TIM3->CNT;
        TIM3->CNT = 0;
        // Run PI controller
        float current = adc_read_current();
        float pwm = pi_controller(target_current - current);
        TIM1->CCR1 = (uint32_t)(pwm * TIM1->ARR);
    }
}
```

### 9.4 ADC 转换完成中断

```c
// ADC1 regular channel, interrupt on EOC
void adc1_init(void) {
    __HAL_RCC_ADC1_CLK_ENABLE();
    ADC1->CR1 = ADC_CR1_EOCIE;  // Enable EOC interrupt
    ADC1->CR2 = ADC_CR2_ADON;
    // Configure channel 1, 480 cycles
    ADC1->SQR3 = 1;
    ADC1->SMPR1 = ADC_SMPR1_SMP1_2 | ADC_SMPR1_SMP1_1;  // 480 cycles
    HAL_NVIC_SetPriority(ADC_IRQn, 5, 0);
    HAL_NVIC_EnableIRQ(ADC_IRQn);
}

volatile uint16_t g_adc_value;

void ADC_IRQHandler(void) {
    if (ADC1->SR & ADC_SR_EOC) {
        g_adc_value = ADC1->DR;  // Read clears EOC
        // Trigger next conversion
        ADC1->CR2 |= ADC_CR2_SWSTART;
    }
}
```

### 9.5 DMA 中断 + 半传输/完成

双缓冲 DMA 用于连续 ADC 采集，半传输和完成中断分别处理前半和后半缓冲：

```c
#define ADC_BUF_SIZE  256
static volatile uint16_t adc_buf[ADC_BUF_SIZE];

void dma_adc_init(void) {
    // DMA1 Stream0, Channel 0 (ADC1), circular, half-word
    DMA1_Stream0->PAR = (uint32_t)&ADC1->DR;
    DMA1_Stream0->M0AR = (uint32_t)adc_buf;
    DMA1_Stream0->NDTR = ADC_BUF_SIZE;
    DMA1_Stream0->CR = DMA_SxCR_CHSEL_0 |    // Channel 0
                       DMA_SxCR_MSIZE_0 |     // 16-bit memory
                       DMA_SxCR_PSIZE_0 |     // 16-bit peripheral
                       DMA_SxCR_MINC |        // Memory increment
                       DMA_SxCR_CIRC |        // Circular
                       DMA_SxCR_HTIE |        // Half-transfer interrupt
                       DMA_SxCR_TCIE |        // Transfer-complete interrupt
                       DMA_SxCR_EN;           // Enable
    HAL_NVIC_SetPriority(DMA1_Stream0_IRQn, 2, 0);
    HAL_NVIC_EnableIRQ(DMA1_Stream0_IRQn);
}

void DMA1_Stream0_IRQHandler(void) {
    BaseType_t hpw = pdFALSE;
    if (DMA1->LISR & DMA_LISR_HTIF0) {
        DMA1->LIFCR = DMA_LIFCR_CHTIF0;
        // First half ready: indices 0..127
        xQueueSendFromISR(half_queue, &adc_buf[0], &hpw);
    }
    if (DMA1->LISR & DMA_LISR_TCIF0) {
        DMA1->LIFCR = DMA_LIFCR_CTCIF0;
        // Second half ready: indices 128..255
        xQueueSendFromISR(full_queue, &adc_buf[128], &hpw);
    }
    portYIELD_FROM_ISR(hpw);
}
```

### 9.6 SPI 从机中断

```c
// SPI2 slave mode, RXNE interrupt
void spi2_slave_init(void) {
    __HAL_RCC_SPI2_CLK_ENABLE();
    SPI2->CR1 = SPI_CR1_CPOL | SPI_CR1_CPHA |  // Mode 3
                SPI_CR1_RXONLY;  // Receive only slave
    SPI2->CR2 = SPI_CR2_RXNEIE;  // RXNE interrupt
    SPI2->CR1 |= SPI_CR1_SPE;    // Enable
    HAL_NVIC_SetPriority(SPI2_IRQn, 4, 0);
    HAL_NVIC_EnableIRQ(SPI2_IRQn);
}

void SPI2_IRQHandler(void) {
    if (SPI2->SR & SPI_SR_RXNE) {
        uint16_t data = SPI2->DR;  // Read clears RXNE
        spi_slave_on_rx(data);
    }
    // MODF (mode fault) - master mode collision
    if (SPI2->SR & SPI_SR_MODF) {
        SPI2->CR1 = SPI2->CR1;  // Clear MODF
        log_error("SPI mode fault");
    }
}
```

### 9.7 CAN 接收中断

```c
// CAN1 RX0 FIFO interrupt
void can1_rx_init(void) {
    CAN1->IER |= CAN_IER_FMPIE0;  // FIFO0 message pending interrupt
    HAL_NVIC_SetPriority(CAN1_RX0_IRQn, 6, 0);
    HAL_NVIC_EnableIRQ(CAN1_RX0_IRQn);
}

void CAN1_RX0_IRQHandler(void) {
    CAN_RxHeaderTypeDef hdr;
    uint8_t data[8];
    if (HAL_CAN_GetRxFifoFillLevel(&hcan1, CAN_RX_FIFO0) > 0) {
        HAL_CAN_GetRxMessage(&hcan1, CAN_RX_FIFO0, &hdr, data);
        // Dispatch by ID
        can_frame_dispatch(&hdr, data);
    }
}
```

### 9.8 外设中断故障排查表

| 现象 | 可能原因 | 排查 |
|------|---------|------|
| ISR 不触发 | NVIC 未使能 / 外设中断未使能 | 检查 NVIC_EnableIRQ 和外设 CR.XXIE |
| ISR 反复触发 | 中断标志未清除 | 确认 ISR 中清除了外设标志 |
| ISR 触发但数据错乱 | 优先级冲突 / 竞态 | 检查共享变量的临界区保护 |
| 偶发 HardFault | ISR 栈溢出 / 非法地址 | 增大栈，检查数组越界 |
| HAL 回调不执行 | HAL_NVIC_SetPriority 未调 | 确认 HAL_Init 后调用 |

## 10. 中断调试技术

### 10.1 SEGGER SystemView 实时跟踪

SEGGER SystemView 是 Cortex-M 上最强大的中断调试工具，可实时记录中断进出时间戳、CPU 占用率、任务切换：

```c
// SystemView integration
#include "SEGGER_SYSVIEW.h"

// In main, after hardware init
SEGGER_SYSVIEW_Conf();
SEGGER_SYSVIEW_Start();

// Mark ISR entry/exit (optional,SEGGER can auto-record via ITM)
void USART1_IRQHandler(void) {
    SEGGER_SYSVIEW_RecordEnterISR();
    // ... ISR body ...
    SEGGER_SYSVIEW_RecordExitISR();
}

// Record custom events
SEGGER_SYSVIEW_Print("Motor overcurrent");
SEGGER_SYSVIEW_RecordVoid(1);  // User event ID 1
```

SystemView 可显示：
- 每个中断的执行时长（最小/最大/平均）
- 中断间嵌套关系时间轴
- CPU 在 ISR vs 任务 vs 空闲的时间分布
- 优先级反转可视化

### 10.2 ITM 跟踪与 printf 重定向

ITM（Instrumentation Trace Macrocell）通过 SWO 引脚输出调试信息，不占用 UART，且在中断中安全：

```c
// ITM printf ( Cortex-M3/M4/M7 with SWO connected debugger)
#define ITM_Port8(n)   (*((volatile uint8_t *)(0xE0000000 + 4*n)))
#define ITM_Port32(n)  (*((volatile uint32_t *)(0xE0000000 + 4*n)))
#define DEMCR          (*((volatile uint32_t *)0xE000EDFC))
#define TRCENA         0x01000000

void itm_putc(char c) {
    if ((DEMCR & TRCENA) && (ITM_Port32(0) & 1)) {
        ITM_Port8(0) = c;
    }
}

void itm_printf(const char *fmt, ...) {
    char buf[128];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    for (int i = 0; i < n; i++) itm_putc(buf[i]);
}

// Safe to use in ISR (non-blocking when SWO buffer has space)
void TIM2_IRQHandler(void) {
    TIM2->SR = ~TIM_SR_UIF;
    itm_printf("tick=%lu\n", HAL_GetTick());
}
```

### 10.3 DWT 周期计数器测 ISR 耗时

```c
// Measure ISR execution time in cycles
static volatile uint32_t isr_cycles[32];
static volatile uint32_t isr_count[32];

#define MEASURE_ISR_START(id)  uint32_t _s = DWT->CYCCNT; (void)id;
#define MEASURE_ISR_END(id)    isr_cycles[id] = DWT->CYCCNT - _s; isr_count[id]++;

void USART1_IRQHandler(void) {
    MEASURE_ISR_START(0);
    // ... ISR body ...
    MEASURE_ISR_END(0);
}

// Periodically report
void report_isr_stats(void) {
    for (int i = 0; i < 32; i++) {
        if (isr_count[i] > 0) {
            printf("ISR %d: avg=%lu cycles, count=%lu\n",
                   i, isr_cycles[i], isr_count[i]);
        }
    }
}
```

### 10.4 HardFault 故障定位全流程

```c
// Comprehensive fault analyzer
typedef struct {
    uint32_t r0, r1, r2, r3, r12, lr, pc, psr;
    uint32_t cfsr, hfsr, mmfar, bfar, lr_exc;
} fault_info_t;

static fault_info_t g_fault;

void HardFault_Handler_C(uint32_t *sp) {
    g_fault.r0  = sp[0]; g_fault.r1 = sp[1];
    g_fault.r2  = sp[2]; g_fault.r3 = sp[3];
    g_fault.r12 = sp[4]; g_fault.lr = sp[5];
    g_fault.pc  = sp[6]; g_fault.psr = sp[7];
    g_fault.cfsr  = SCB->CFSR;
    g_fault.hfsr  = SCB->HFSR;
    g_fault.mmfar = SCB->MMFAR;
    g_fault.bfar  = SCB->BFAR;
    g_fault.lr_exc = __get_LR();

    // Decode
    if (g_fault.hfsr & (1 << 30)) {
        // FORCED: configurable fault escalated
        if (g_fault.cfsr & 0xFF) {  // MMFSR
            if (g_fault.cfsr & 1) log_fault("IACCVIOL @ PC=0x%08X", g_fault.pc);
        }
        if (g_fault.cfsr & 0xFF00) {  // BFSR
            if (g_fault.cfsr & (1 << 9)) log_fault("IMPRECISERR");
            if (g_fault.cfsr & (1 << 8)) log_fault("PRECISERR @ 0x%08X", g_fault.bfar);
        }
        if (g_fault.cfsr & 0xFFFF0000) {  // UFSR
            uint16_t uf = g_fault.cfsr >> 16;
            if (uf & (1 << 9)) log_fault("DIVBYZERO @ PC=0x%08X", g_fault.pc);
            if (uf & 1)        log_fault("UNDEFINSTR @ PC=0x%08X", g_fault.pc);
        }
    }
    if (g_fault.hfsr & (1 << 31)) {
        log_fault("VECTTBL: vector table read fault");
    }

    // Find offending line via addr2line
    // arm-none-eabi-addr2line -e firmware.elf 0x08001234
    while (1) {}
}
```

### 10.5 调试器断点技巧

- **条件断点**：在 ISR 中设断点会大幅改变时序（暂停时外设继续运行）。用条件断点减少触发：`g_rx_count == 100`。
- **数据观察点（Watchpoint）**：DWT 支持硬件数据断点，监控变量被写入时不暂停 CPU（仅记录），适合跟踪竞态。
- **日志缓冲区**：ISR 中写入环形日志缓冲区，主循环输出，避免在 ISR 中调用 printf。

```c
// Lightweight ISR logger
#define LOG_SIZE 64
typedef struct { uint32_t tick; uint16_t id; uint16_t val; } log_entry_t;
static log_entry_t log_buf[LOG_SIZE];
static volatile uint16_t log_head = 0;

#define ISR_LOG(id, val) do { \
    uint16_t h = log_head; \
    log_buf[h].tick = HAL_GetTick(); \
    log_buf[h].id = (id); \
    log_buf[h].val = (val); \
    log_head = (h + 1) % LOG_SIZE; \
} while(0)

void USART1_IRQHandler(void) {
    if (USART1->SR & USART_SR_RXNE) {
        uint8_t b = USART1->DR;
        ISR_LOG(1, b);
        ring_put(&rx_ring, b);
    }
}
```

### 10.6 中断丢失检测

```c
// Detect missed interrupts by counting pending vs served
static volatile uint32_t exti_pending_cnt = 0;
static volatile uint32_t exti_served_cnt = 0;

void EXTI0_IRQHandler(void) {
    exti_served_cnt++;
    EXTI->PR = EXTI_LINE_0;
    // ... handle ...
}

// In main loop, check for missed
void check_missed_interrupts(void) {
    if (exti_served_cnt < exti_pending_cnt) {
        log_error("Missed %lu EXTI0 interrupts",
                  exti_pending_cnt - exti_served_cnt);
    }
}
```

---

## 11. 中断性能优化

### 11.1 ISR 编写准则

1. **短小**：目标 < 50µs，理想 < 10µs。
2. **无阻塞**：不调用 malloc、printf、HAL_Delay、任何带 timeout 的 API。
3. **无锁**：避免获取互斥量（会阻塞）。用 lock-free 数据结构（环形缓冲区）。
4. **清除标志优先**：进入 ISR 第一件事清除中断源标志，减少重入风险。
5. **volatile**：ISR 与主循环共享的变量必须声明 volatile 或用内存屏障。

```c
// Optimal ISR pattern
void USART1_IRQHandler(void) {
    uint32_t sr = USART1->SR;  // Read status once
    if (sr & USART_SR_RXNE) {
        uint8_t b = USART1->DR;  // Read DR clears RXNE
        ring_put(&rx_ring, b);   // Lock-free ring buffer
    }
    if (sr & USART_SR_IDLE) {
        (void)USART1->DR;        // Clear IDLE
        frame_ready = 1;         // Signal main loop
    }
    // No printf, no HAL_Delay, no malloc
}
```

### 11.2 减少压栈开销

硬件自动压栈 8 寄存器（32 字节）无法避免，但可优化：

- **ISR 不用 FPU**：避免额外的 104 字节 FPU 上下文保存。用 `__attribute__((pcs("naked")))` 或在 ISR 入口禁用 FPU（CCR）。
- **合并 ISR**：若多个中断共享一个 ISR（如 EXTI9_5），合并处理减少进出开销。

```c
// Disable lazy FPU stacking for ISR (saves ~16 cycles per entry)
// In SystemInit:
SCB->CCR &= ~SCB_CCR_FP_CA_Msk;  // Disable automatic FPU context save
// But ensure ISR doesn't use FP registers, else HardFault
```

### 11.3 ISR 放 SRAM 执行

对延迟极敏感的 ISR（电机电流环），放到 SRAM 消除 Flash 等待：

```c
// GCC: place function in .ramfunc section
__attribute__((section(".ramfunc"), noinline))
void TIM1_UP_TIM10_IRQHandler(void) {
    TIM1->SR = ~TIM_SR_UIF;
    // Motor FOC loop - runs from SRAM, zero wait state
    float i_a = ADC1->DR;
    float i_b = ADC2->DR;
    // ... Clarke/Park transform ...
}

// Linker script must copy .ramfunc from Flash to RAM at startup:
// _sramfunc = .; *(.ramfunc) _eramfunc = .;
// And in Reset_Handler: memcpy(_sramfunc, _sramfunc_load, _eramfunc - _sramfunc);
```

### 11.4 DMA 替代中断搬运

每字节一次中断的 UART 在高波特率下吃掉大量 CPU。改用 DMA + 空闲中断：

```c
// UART DMA RX with IDLE line detection - only 1 interrupt per frame
void uart_dma_rx_init(void) {
    // Enable DMA for UART RX
    USART1->CR3 |= USART_CR3_DMAR;
    // Configure DMA (circular)
    DMA2_Stream2->CR = /* ... circular, periph->mem ... */;
    DMA2_Stream2->NDTR = BUF_SIZE;
    DMA2_Stream2->M0AR = (uint32_t)rx_buf;
    DMA2_Stream2->PAR = (uint32_t)&USART1->DR;
    DMA2_Stream2->CR |= DMA_SxCR_EN;
    // Enable IDLE interrupt (fires on end of frame)
    USART1->CR1 |= USART_CR1_IDLEIE;
    HAL_NVIC_EnableIRQ(USART1_IRQn);
}

void USART1_IRQHandler(void) {
    if (USART1->SR & USART_SR_IDLE) {
        (void)USART1->DR;  // Clear IDLE
        // Frame received: bytes = BUF_SIZE - DMA2_Stream2->NDTR
        uint16_t len = BUF_SIZE - DMA2_Stream2->NDTR;
        process_frame(rx_buf, len);
        // Reset DMA for next frame
        DMA2_Stream2->CR &= ~DMA_SxCR_EN;
        DMA2_Stream2->NDTR = BUF_SIZE;
        DMA2_Stream2->CR |= DMA_SxCR_EN;
    }
}
```

### 11.5 优先级抖动消除

硬实时系统需消除中断抖动（响应时间变化）。措施：

1. **关 DMA 突发**：DMA 突发占用总线导致 CPU 取指延迟。对关键 ISR 用 SRAM + 限制 DMA 优先级。
2. **避免多周期指令**：在关键路径前不执行 SDIV/LDM。
3. **WFE 替代 WFI**：WFE（Wait For Event）唤醒更快，但需配合 SEV。
4. **禁用缓存污染**：M7 的 L1 缓存可能被 DMA 数据污染，关键 ISR 用缓存锁定。

### 11.6 CPU 占用率测量

```c
// Measure CPU load: idle task counts cycles, compare to total
static volatile uint32_t idle_count = 0;
static volatile uint32_t max_idle_count = 0;

void vApplicationIdleHook(void) {
    // Runs only when no task ready and no ISR
    // Count iterations in a fixed window
    uint32_t start = HAL_GetTick();
    uint32_t cnt = 0;
    while (HAL_GetTick() - start < 1000) {  // 1 second window
        cnt++;
        __WFI();  // Sleep to save power
    }
    idle_count = cnt;
    if (cnt > max_idle_count) max_idle_count = cnt;
}

// CPU load = (1 - idle_count / max_idle_count) * 100%
// max_idle_count is the baseline (0% load) measured at startup
float get_cpu_load(void) {
    return (1.0f - (float)idle_count / max_idle_count) * 100.0f;
}
```

---

## 12. 常见问题 FAQ

### Q1: 为什么我的 ISR 只触发一次？

**A**: 90% 是中断标志未清除。Cortex-M NVIC 挂起位会自动清除，但外设标志（如 USART SR.RXNE、TIM SR.UIF）必须软件清除，否则退出后立即重入或挂起。检查 ISR 是否在处理前清除了外设标志。

### Q2: ISR 中可以调用 HAL_Delay 吗？

**A**: **不可以**。HAL_Delay 依赖 SysTick 中断递增 uwTick，若 ISR 优先级高于或等于 SysTick，SysTick 无法执行，HAL_Delay 死循环。即使优先级低于 SysTick，长时间 Delay 也会阻塞其他中断。

### Q3: 为什么 FreeRTOS 任务切换偶尔卡死？

**A**: 常见原因：
1. 优先级高于 configMAX_SYSCALL_INTERRUPT_PRIORITY 的中断调用了 FromISR API。开启 configASSERT 捕获。
2. PendSV/SysTick 优先级设置错误（必须设为最低）。
3. 临界区内调用了阻塞 API。
4. 栈溢出（开启 configCHECK_FOR_STACK_OVERFLOW）。

### Q4: 中断优先级设成 0 和设成 15 哪个更高？

**A**: **0 更高**。Cortex-M 优先级数值越小优先级越高。0 是最高可编程优先级（Reset/NMI/HardFault 除外，它们是负数）。15 是最低。这是初学者最常犯的错误。

### Q5: 如何在 ISR 中安全地调用 printf？

**A**: 不建议直接调用（printf 可能阻塞、非可重入）。三种替代：
1. ITM printf（通过 SWO，非阻塞）。
2. 写入日志环形缓冲区，主循环输出。
3. 触发低优先级中断（PendSV）在非实时上下文打印。

### Q6: 同一中断能被自己抢占吗？

**A**: 默认不能。NVIC 活跃位阻止同优先级中断重入。若确需重入，需在 ISR 中手动清除挂起位并临时降低自身优先级，极其危险不推荐。

### Q7: 为什么 BASEPRI 设了值后中断还是会被抢占？

**A**: BASEPRI 只屏蔽优先级数值 **大于等于** BASEPRI 的中断。数值更小（更高优先级）的中断仍能响应。如果设 BASEPRI=5，优先级 0-4 的中断不被屏蔽。这是设计特性，用于"保留高优先级中断实时性"。

### Q8: HardFault_Handler 里可以做什么？

**A**: 极有限。此时系统状态可能已损坏。安全做法：读取故障寄存器存入非易失存储或全局变量，然后复位（NVIC_SystemReset）或死循环等待调试器。**不要**调用 malloc/printf 等可能进一步触发 fault 的函数。用 ITM 或简单内存写入记录最安全。

### Q9: 如何让某个中断不响应但记录它的发生？

**A**: 禁用 NVIC 使能但保持外设中断使能，挂起位会记录事件：

```c
NVIC_DisableIRQ(USART1_IRQn);  // NVIC won't activate
// USART1->CR1 |= RXNEIE still set, flag goes to pending
// Periodically check:
if (NVIC_GetPendingIRQ(USART1_IRQn)) {
    // A byte arrived, handle it manually
    NVIC_ClearPendingIRQ(USART1_IRQn);
}
```

### Q10: 多核 Cortex-M（如 M33 双核）如何处理核间中断？

**A**: 用 SGIs（Software Generated Interrupts）或厂商提供的核间邮箱。STM32H7 双核用 HSEM（硬件信号量）+ C1/C2 邮箱触发对方核的中断。

### Q11: WFI 和 WFE 的区别？

**A**: WFI（Wait For Interrupt）等到任意中断唤醒；WFE（Wait For Event）等到事件唤醒，事件可由 SEV 指令或中断产生。WFE 有内部事件锁存，可能立即返回（如果之前有未消费事件）。低功耗建议 WFI，多核同步用 WFE+SEV。

### Q12: 为什么从 STOP 模式唤醒后中断延迟很大？

**A**: STOP 模式关闭 PLL，唤醒后需重新启动 PLL 锁定（几毫秒）。这段时间中断仍会响应但用 HSI 时钟（慢）。如需低延迟唤醒，用 SLEEP 模式（仅停 CPU，时钟保持）或配置 FCLK 在 STOP 时保持。

---

## 13. 不同 MCU 中断实现对比

### 13.1 STM32 系列

| 系列 | 内核 | 优先级位数 | 特色 |
|------|------|-----------|------|
| STM32F0 | M0 | 2（4级） | 无尾链优化，延迟 15 cycles |
| STM32F1 | M3 | 4（16级） | 主流入门 |
| STM32F4 | M4F | 4（16级） | FPU，ART 加速 |
| STM32F7 | M7 | 4（16级） | L1 缓存，双发射 |
| STM32H7 | M7 | 4（16级） | 双核（M7+M4），480MHz |
| STM32G0 | M0+ | 2（4级） | 低功耗 |
| STM32G4 | M4F | 4（16级） | 模拟外设丰富 |
| STM32L4 | M4F | 4（16级） | 超低功耗 |
| STM32U5 | M33 | 4（16级） | TrustZone，安全 |

STM32 HAL 提供统一 API（HAL_NVIC_*），但底层优先级位数不同，迁移时需重新分配优先级数值。

### 13.2 STM32 EXTI 与 NVIC 的关系

STM32 的 EXTI 是 GPIO 中断的"前置筛选器"：EXTI 监测 GPIO 边沿，触发后向 NVIC 发对应 IRQn。EXTI0-4 各有独立 IRQn（EXTI0_IRQn...EXTI4_IRQn），EXTI5-9 共享 EXTI9_5_IRQn，EXTI10-15 共享 EXTI15_10_IRQn。共享 IRQ 的 ISR 需查询哪个线触发：

```c
void EXTI9_5_IRQHandler(void) {
    if (EXTI->PR & EXTI_LINE_5)  { EXTI->PR = EXTI_LINE_5;  handle_line5(); }
    if (EXTI->PR & EXTI_LINE_6)  { EXTI->PR = EXTI_LINE_6;  handle_line6(); }
    // ... up to line 9
}
```

### 13.3 ESP32 中断模型对比

ESP32（Xtensa LX6/LX7）与 Cortex-M 中断模型差异显著：

| 特性 | Cortex-M4 | ESP32（Xtensa） |
|------|-----------|----------------|
| 中断控制器 | NVIC（内置） | INTC（外置式） |
| 优先级 | 数值小即高 | 数值小即高（1-7） |
| 优先级位数 | 4-8 | 3（7级） |
| 压栈 | 硬件自动 | 部分软件 |
| 向量化 | 硬件跳转 | 软件分发 |
| 延迟 | 12 cycles | ~20-40 cycles |
| 多核 | 单核（除 H7） | 双核，每核独立 INTC |

ESP32 用 `IRAM_ATTR` 把 ISR 放 IRAM，类似 Cortex-M 的 SRAM ISR。

### 13.4 Arduino AVR 中断对比

8 位 AVR（ATmega328）的中断模型是 Cortex-M 的"简化版"：

| 特性 | Cortex-M4 | AVR |
|------|-----------|-----|
| 中断数 | 240 | ~26 |
| 优先级 | 16 级抢占 | 仅嵌套（无优先级，按向量顺序） |
| 压栈 | 硬件 8 寄存器 | 软件压栈（ISR 开头 push） |
| 向量表 | Flash/SRAM | Flash 固定 |
| 延迟 | 12 cycles | ~4-8 cycles（时钟简单） |

AVR 的 `attachInterrupt()` 是软件封装，开销大。直接写 ISR 更高效。

### 13.5 Nordic nRF52（M4F）特殊点

nRF52 系列用 Cortex-M4F，但有几个特殊设计：
- **软设备（SoftDevice）**：BLE 协议栈以高优先级中断运行，应用中断必须用 SD_IRQ 优先级兼容（通常 ≤ 2 被 SD 占用）。
- **外设中断合并**：多个外设共享 IRQn（如 SPIM0/SPIS0/TWIM0/TWIS0 共享 SPIM0_SPIS0_TWIM0_TWIS0_SPI0_TWI0_IRQn），ISR 需查事件寄存器分发。
- **POWER 时钟模块**：外设需先通过 POWER 寄存器供电才能产生中断。

### 13.6 跨平台中断代码移植建议

1. **优先级数值翻转**：从 RTOS（任务优先级大即高）迁移到 Cortex-M（中断优先级小即高）时务必注意。
2. **优先级位数差异**：AVR 2 级 vs Cortex-M 16 级，重新设计优先级层次。
3. **ISR 签名**：Cortex-M ISR 是 `void f(void)`，AVR 是 `ISR(VECTOR)`，ESP32 是 `void IRAM_ATTR f(void*)`。
4. **向量表**：Cortex-M 硬件向量化，其他平台可能需软件分发。
5. **FPU**：M4F/M7 有硬件 FPU，AVR/ESP32 部分型号无 FPU，浮点 ISR 性能差异大。

### 13.7 中断安全检查清单

移植或新写中断代码时逐项检查：

- [ ] ISR 函数名与启动文件向量表一致
- [ ] NVIC 优先级在 configMAX_SYSCALL_INTERRUPT_PRIORITY 之上则不调用 RTOS API
- [ ] ISR 中清除了所有触发的中断标志
- [ ] 共享变量声明 volatile 或加内存屏障
- [ ] ISR 中无 malloc/printf/HAL_Delay 等阻塞调用
- [ ] 临界区（PRIMASK/BASEPRI）成对使用
- [ ] 栈足够深（含嵌套 + FPU）
- [ ] 优先级数值方向正确（小即高）
- [ ] 向量表对齐（VTOR 要求）
- [ ] HardFault_Handler 能记录故障信息

---

## 附录 A：NVIC 寄存器速查

| 寄存器 | 地址 | 作用 | CMSIS 函数 |
|--------|------|------|-----------|
| ISER0-7 | 0xE000E100 | 使能置位 | NVIC_EnableIRQ |
| ICER0-7 | 0xE000E180 | 使能清除 | NVIC_DisableIRQ |
| ISPR0-7 | 0xE000E200 | 挂起置位 | NVIC_SetPendingIRQ |
| ICPR0-7 | 0xE000E280 | 挂起清除 | NVIC_ClearPendingIRQ |
| IABR0-7 | 0xE000E300 | 活跃位（只读） | NVIC_GetActive |
| IP0-239 | 0xE000E400 | 优先级 | NVIC_SetPriority |
| STIR | 0xE000EF00 | 软件触发 | NVIC_SetPendingIRQ |

## 附录 B：SCB 关键寄存器

| 寄存器 | 地址 | 作用 |
|--------|------|------|
| CPUID | 0xE000ED00 | CPU 标识 |
| ICSR | 0xE000ED04 | 中断控制状态（VECTACTIVE/VECTPENDING/PENDSVSET/NMIPENDSET） |
| VTOR | 0xE000ED08 | 向量表偏移 |
| AIRCR | 0xE000ED0C | 中断优先级分组（PRIGROUP） |
| SCR | 0xE000ED10 | 睡眠控制（SLEEPDEEP/SEVONPEND） |
| CCR | 0xE000ED14 | 配置控制（DIV_0_TRP/UNALIGN_TRP） |
| SHPR1-3 | 0xE000ED18 | 系统异常优先级 |
| SHCSR | 0xE000ED24 | 系统异常使能/状态 |
| CFSR | 0xE000ED28 | 可配置故障状态 |
| HFSR | 0xE000ED2C | 硬故障状态 |
| MMFAR | 0xE000ED34 | MemManage 故障地址 |
| BFAR | 0xE000ED38 | BusFault 故障地址 |

## 附录 C：优先级分组与 EXC_RETURN 速查

EXC_RETURN 值含义见第 1.5 节。优先级分组（STM32F4，4 位实现）：

| HAL 宏 | PRIGROUP | 抢占位数 | 子优先级位数 |
|--------|----------|---------|------------|
| NVIC_PRIORITYGROUP_0 | 7 | 0 | 4 |
| NVIC_PRIORITYGROUP_1 | 6 | 1 | 3 |
| NVIC_PRIORITYGROUP_2 | 5 | 2 | 2 |
| NVIC_PRIORITYGROUP_3 | 4 | 3 | 1 |
| NVIC_PRIORITYGROUP_4 | 3 | 4 | 0 |

## 附录 D：故障状态寄存器解码表

**CFSR.MMFSR（低 8 位）**：

| 位 | 名称 | 含义 |
|----|------|------|
| 0 | IACCVIOL | 指令访问违规（XN 区执行） |
| 1 | DACCVIOL | 数据访问违规（MPU 限制） |
| 3 | MUNSTKERR | 出栈时 MPU 违规 |
| 4 | MSTKERR | 压栈时 MPU 违规 |
| 7 | MMARVALID | MMFAR 有效 |

**CFSR.BFSR（[15:8]）**：

| 位 | 名称 | 含义 |
|----|------|------|
| 0 | IBUSERR | 取指总线错误 |
| 1 | PRECISERR | 精确数据总线错误 |
| 2 | IMPRECISERR | 非精确数据总线错误 |
| 3 | UNSTKERR | 出栈时总线错误 |
| 4 | STKERR | 压栈时总线错误 |
| 7 | BFARVALID | BFAR 有效 |

**CFSR.UFSR（[31:16]）**：

| 位 | 名称 | 含义 |
|----|------|------|
| 0 | UNDEFINSTR | 未定义指令 |
| 1 | INVSTATE | 无效状态（T 位错） |
| 2 | INVPC | PC 加载异常 |
| 3 | NOCP | 协处理器不可用 |
| 8 | UNALIGNED | 未对齐访问 |
| 9 | DIVBYZERO | 除零 |

**HFSR**：

| 位 | 名称 | 含义 |
|----|------|------|
| 30 | FORCED | 可配置 fault 升级为 HardFault |
| 31 | VECTTBL | 向量表读取失败 |

## 附录 E：向量表模板（STM32F4 节选）

```c
__attribute__((section(".isr_vector")))
void (* const g_pfnVectors[])(void) = {
    (void (*)(void))0x20020000,   // Initial MSP
    Reset_Handler,
    NMI_Handler,
    HardFault_Handler,
    MemManage_Handler,
    BusFault_Handler,
    UsageFault_Handler,
    0, 0, 0, 0,                   // Reserved
    SVC_Handler,
    DebugMon_Handler,
    0,                             // Reserved
    PendSV_Handler,
    SysTick_Handler,
    // External IRQs
    WWDG_IRQHandler,               // IRQ0
    PVD_IRQHandler,                // IRQ1
    TAMP_STAMP_IRQHandler,         // IRQ2
    RTC_WKUP_IRQHandler,           // IRQ3
    FLASH_IRQHandler,              // IRQ4
    RCC_IRQHandler,                // IRQ5
    EXTI0_IRQHandler,              // IRQ6
    EXTI1_IRQHandler,              // IRQ7
    EXTI2_IRQHandler,              // IRQ8
    EXTI3_IRQHandler,              // IRQ9
    EXTI4_IRQHandler,              // IRQ10
    DMA1_Stream0_IRQHandler,       // IRQ11
    // ... up to IRQ81 ...
};
```

## 附录 F：中断延迟测量代码全集

```c
// Complete latency measurement setup
#include "stm32f4xx.h"

static volatile uint32_t trigger_cyc;
static volatile uint32_t latency_cyc;
static volatile uint32_t max_latency = 0;
static volatile uint32_t min_latency = 0xFFFFFFFF;

void dwt_init(void) {
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
}

// Trigger: set by main before external stimulus
void latency_arm(void) {
    trigger_cyc = DWT->CYCCNT;
}

void EXTI0_IRQHandler(void) {
    uint32_t now = DWT->CYCCNT;
    latency_cyc = now - trigger_cyc;
    if (latency_cyc > max_latency) max_latency = latency_cyc;
    if (latency_cyc < min_latency) min_latency = latency_cyc;
    EXTI->PR = EXTI_LINE_0;
}

void latency_report(void) {
    printf("Latency: min=%lu max=%lu last=%lu cycles\n",
           min_latency, max_latency, latency_cyc);
    printf("At %lu Hz: min=%lu ns max=%lu ns\n",
           SystemCoreClock,
           (min_latency * 1000000000ULL) / SystemCoreClock,
           (max_latency * 1000000000ULL) / SystemCoreClock);
}
```

## 附录 G：FreeRTOS 中断优先级配置模板

```c
// FreeRTOSConfig.h for STM32F4 (4 priority bits)
#define configPRIO_BITS                              4
#define configLIBRARY_LOWEST_INTERRUPT_PRIORITY      15
#define configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY 5

#define configKERNEL_INTERRUPT_PRIORITY \
        (configLIBRARY_LOWEST_INTERRUPT_PRIORITY << (8 - configPRIO_BITS))
#define configMAX_SYSCALL_INTERRUPT_PRIORITY \
        (configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY << (8 - configPRIO_BITS))

// Application IRQ assignments (priority must be >= 5 to call RTOS API)
// Priority 0-4: NO RTOS API allowed (raw hardware ISRs only)
// Priority 5-15: RTOS API allowed
#define IRQ_PRIO_SYSTICK     15  // Lowest, RTOS tick
#define IRQ_PRIO_PENDSV      15  // Lowest, context switch
#define IRQ_PRIO_MOTOR_TIM   1   // High, no RTOS API
#define IRQ_PRIO_DMA         5   // Can use RTOS API
#define IRQ_PRIO_UART_RX     6
#define IRQ_PRIO_SPI         7
#define IRQ_PRIO_BUTTON      12
```

## 附录 H：临界区实现对比

```c
// Method 1: PRIMASK (disable all IRQs) - simple but blocks everything
uint32_t primask_save = __get_PRIMASK();
__disable_irq();
// ... critical section ...
__set_PRIMASK(primask_save);

// Method 2: BASEPRI (disable IRQs >= threshold) - RTOS friendly
uint32_t basepri_save = __get_BASEPRI();
__set_BASEPRI(configMAX_SYSCALL_INTERRUPT_PRIORITY);
// ... critical section ...
__set_BASEPRI(basepri_save);

// Method 3: FreeRTOS wrapper (reentrant, uses BASEPRI)
taskENTER_CRITICAL();
// ... critical section ...
taskEXIT_CRITICAL();

// Method 4: FreeRTOS ISR-safe wrapper
UBaseType_t uxSaved = taskENTER_CRITICAL_FROM_ISR();
// ... critical section in ISR ...
taskEXIT_CRITICAL_FROM_ISR(uxSaved);
```

## 附录 I：中断与 DMA 联合设计决策表

| 数据速率 | 推荐 | ISR 负载 | 说明 |
|---------|------|---------|------|
| < 1 KB/s | 字节中断 | 中 | 简单，如按键 |
| 1-100 KB/s | DMA + 半/全中断 | 低 | 平衡 |
| > 100 KB/s | DMA 循环 + 空闲 | 极低 | 高吞吐 |
| 实时控制 | DMA + 定时触发 | 低 | 确定性 |

## 附录 J：Code Review 中断检查清单

**初始化**：
- [ ] 中断优先级分组在启动时统一设置（HAL_NVIC_SetPriorityGrouping）
- [ ] 所有 HAL_NVIC_SetPriority 在外设初始化时调用
- [ ] 外设中断使能位与 NVIC 使能位都设置
- [ ] 向量表地址正确（VTOR 设置，bootloader/app 切换）

**ISR 实现**：
- [ ] ISR 名与启动文件向量表匹配
- [ ] ISR 入口清除中断源标志（外设 SR/PR）
- [ ] ISR 中无阻塞调用（无 HAL_Delay/malloc/printf）
- [ ] 共享变量用 volatile 或加 __DSB/__ISB
- [ ] ISR 执行时间 < 50µs（用 DWT 测量）
- [ ] 中断优先级数值方向正确（小即高）

**RTOS 集成**：
- [ ] 优先级 ≥ configMAX_SYSCALL_INTERRUPT_PRIORITY 才调用 FromISR API
- [ ] FromISR API 配合 portYIELD_FROM_ISR
- [ ] 临界区成对（taskENTER/EXIT_CRITICAL）
- [ ] 不在临界区内调用阻塞 API
- [ ] PendSV/SysTick 优先级设为最低

**故障处理**：
- [ ] HardFault_Handler 记录 PC/CFSR/HFSR
- [ ] 使能可配置 fault（BusFault/UsageFault）以便定位
- [ ] 栈深度足够（含嵌套 + FPU）

## 附录 K：常见 HardFault 原因速查

| 现象（CFSR 位） | 根因 | 排查 |
|----------------|------|------|
| UNDEFINSTR | 函数指针错误/跳转到数据 | 检查向量表/回调指针 |
| INVSTATE | Thumb 位丢失 | 函数指针未用 `void(*)(void)` 强转 |
| INVPC | 异常返回 LR 损坏 | 栈溢出破坏栈帧 |
| UNALIGNED | 未对齐访问 | 检查结构体对齐、强制转换 |
| DIVBYZERO | 整数除零 | 检查除数 |
| PRECISERR | 外设未使能时钟就访问 | 检查 RCC 时钟使能 |
| IMPRECISERR | 写缓冲延迟错误 | 查最近写操作 |
| IBUSERR | 取指失败 | 代码区地址错/MPU |
| FORCED | 上述升级 | 解码 CFSR 子字段 |
| VECTTBL | 向量表读取失败 | VTOR 设置/对齐 |

## 附录 L：中断相关内联函数速查

```c
__enable_irq();           // Clear PRIMASK
__disable_irq();          // Set PRIMASK
__get_PRIMASK();          // Read PRIMASK
__set_PRIMASK(x);
__get_BASEPRI(); __set_BASEPRI(x);
__get_FAULTMASK(); __set_FAULTMASK(x);
__get_MSP(); __set_MSP(x);
__get_PSP(); __set_PSP(x);
__get_CONTROL(); __set_CONTROL(x);
__get_LR();
__ISB();  // Instruction Synchronization Barrier
__DSB();  // Data Synchronization Barrier
__DMB();  // Data Memory Barrier
__WFI();  // Wait For Interrupt
__WFE();  // Wait For Event
__SEV();  // Send Event
__CLZ(x); // Count Leading Zeros
__REV(x); // Byte reverse word
NVIC_SystemReset();  // Soft reset
```

## 附录 M：中断延迟优化决策树

```
延迟过高？
├─ ISR 在 Flash？ → 移到 SRAM（__RAM_FUNC）
├─ ISR 用 FPU？ → 禁用 FPU 上下文保存或避免浮点
├─ 有多周期指令？ → 替换 SDIV/LDM
├─ 临界区过长？ → 缩短 BASEPRI 临界区
├─ DMA 抢总线？ → 限制 DMA 优先级/突发长度
├─ 嵌套过深？ → 减少抢占层级
├─ 缓存未命中？ → M7 锁定关键 ISR 到缓存
└─ 仍不够？ → 评估是否需要更高性能 MCU 或硬件加速
```

## 附录 N：术语对照

| 英文 | 中文 |
|------|------|
| Exception | 异常 |
| Interrupt | 中断 |
| NVIC | 嵌套向量中断控制器 |
| Preemption | 抢占 |
| Preempting Priority | 抢占优先级 |
| Subpriority | 子优先级 |
| Tail-Chaining | 尾链 |
| Late-Arrival | 迟来 |
| Stacking | 压栈 |
| Unstacking | 出栈 |
| EXC_RETURN | 异常返回值 |
| Vector Table | 向量表 |
| Pending | 挂起 |
| Active | 活跃 |
| Latency | 延迟 |
| Jitter | 抖动 |
| Critical Section | 临界区 |
| Priority Inversion | 优先级反转 |
| Priority Ceiling | 优先级天花板 |
| Context Switch | 上下文切换 |
| Fault | 故障 |
| HardFault | 硬故障 |
| BusFault | 总线故障 |
| MemManage | 内存管理故障 |
| UsageFault | 用法故障 |
| Privileged | 特权 |
| Unprivileged | 非特权 |
| Thread Mode | 线程模式 |
| Handler Mode | 处理模式 |

## 附录 O：推荐工具

| 工具 | 用途 | 平台 |
|------|------|------|
| SEGGER SystemView | 实时中断跟踪 | Cortex-M3/4/7 |
| SEGGER Ozone | 调试器 + 实时分析 | Cortex-M |
| Keil MDK Logic Analyzer | GPIO 时序可视化 | Cortex-M |
| STM32CubeMonitor | 变量实时监控 | STM32 |
| OpenOCD + GDB | 开源调试 | 多平台 |
| sigrok | 逻辑分析仪协议解码 | 跨平台 |
| Percepio Tracealyzer | RTOS 跟踪 | 多 RTOS |

## 附录 P：中断性能基准参考值

STM32F4 @ 168MHz 实测（参考）：

| 操作 | 耗时（cycles） | 耗时（ns） |
|------|---------------|-----------|
| 中断进入（Flash + ART） | 18-25 | 107-149 |
| 中断进入（SRAM） | 12-15 | 71-89 |
| 尾链切换 | 6 | 36 |
| PendSV 上下文切换 | 200-400 | 1190-2380 |
| xQueueSendFromISR | 150-300 | 893-1786 |
| taskENTER_CRITICAL | 10-20 | 60-119 |
| GPIO 翻转（BSRR） | 1-2 | 6-12 |
| HAL_GetTick | 5-10 | 30-60 |

## 文档版本说明

本文档系统覆盖 ARM Cortex-M 系列处理器的中断与异常处理，从异常模型、NVIC、PRIGROUP 优先级分组、12 周期中断延迟、尾链与迟来优化、向量表重定位、故障处理（HardFault/BusFault/UsageFault）、FreeRTOS 集成、外设中断实战（EXTI/UART/ADC/DMA/SPI/CAN）、调试技术（SystemView/ITM/DWT）、性能优化，到不同 MCU（STM32/ESP32/AVR/nRF52）中断实现对比，包含 13 个主章节与 16 个附录（A-P），可作为嵌入式实时系统中断开发的实战参考手册。

---

## 14. Cortex-M 调试与追踪系统详解

调试与追踪系统是 Cortex-M 区别于早期 ARM 内核的重要特性。它不仅支持传统的断点/单步调试，还提供实时追踪能力，可在不干扰 CPU 执行的前提下捕获中断进出、数据访问、性能计数等事件。本章系统讲解 SWD/JTAG 接口、ITM、DWT、ETM、MTB 五大组件，并给出 OpenOCD/ST-Link/J-Link 的实战配置。

### 14.1 调试接口对比：SWD vs JTAG

Cortex-M 提供两种调试端口：JTAG-DP（Debug Port）和 SW-DP（Serial Wire Debug Port）。SWD 是 ARM 推荐的低引脚数方案，绝大多数 STM32 板卡默认使用 SWD。

| 特性 | JTAG | SWD |
|------|------|-----|
| 引脚数 | 5（TCK/TMS/TDI/TDO/TRST） | 2（SWCLK/SWDIO） |
| 最大时钟 | 10 MHz | 10 MHz |
| 追踪支持 | 无（需额外 ETM 引脚） | SWO（单引脚追踪） |
| 多核调试 | 支持（TAP 链） | 单核为主，多核需扩展 |
| Cortex-M0/M0+ | 部分支持 | 推荐 |
| Cortex-M3/M4/M7 | 支持 | 推荐 |
| 引脚复用 | 占用多 | 仅 2 引脚，可与 GPIO 复用 |

实际开发中，SWD 因仅需 SWCLK/SWDIO 两根线（外加 GND/RESET/VREF）即可完成下载与调试，已成为 STM32 板卡标配。SWO（Serial Wire Output）作为 SWD 的扩展，提供单引脚的追踪输出能力，可输出 ITM 数据。

引脚连接示意：

```
Debugger          Target (STM32)
─────────         ──────────────
SWCLK  ─────────── PA14 (SWCLK)
SWDIO  ─────────── PA13 (SWDIO)
SWO    ─────────── PB3  (SWO, optional for trace)
GND    ─────────── GND
VREF   ─────────── VDD (3.3V, reference)
RESET  ─────────── NRST (optional)
```

### 14.2 调试端口（DP）与访问端口（AP）

Cortex-M 调试架构分为两层：DP（Debug Port）负责与调试器通信，AP（Access Port）负责访问内部总线。常用 AP：

- **APB-AP**：访问 APB 总线（系统控制空间 SCS、NVIC、SCB 等）。
- **AHB-AP**：访问 AHB 总线（内存、外设），最常用。
- **JTAG-AP**：访问传统 JTAG 外设（很少用）。

调试器读内存的流程：调试器通过 SWD 协议写 DP 寄存器选择 APB-AP，再写 AP 寄存器设置目标地址，最后读 AP 的 DRW 寄存器获得数据。这一过程对 CPU 透明（CPU 可继续执行），是"非侵入式"调试的基础。

### 14.3 ITM（Instrumentation Trace Macrocell）使用详解

ITM 是 Cortex-M3/M4/M7 提供的应用级追踪单元，通过 SWO 引脚输出 32 个软件刺激端口（Stimulus Port）的数据，适合在 ISR 中输出调试日志而不影响实时性。

ITM 核心特性：
- 32 个独立刺激端口（Port 0-31），每个端口可配置为是否允许软件写入。
- 硬件时间戳：每条 ITM 数据可附带时间戳，便于分析时序。
- 与 DWT 联动：DWT 的事件（如数据访问匹配）可触发 ITM 输出。
- 同步输出：ITM 数据带协议帧，调试器可解析端口号与时间戳。

ITM 寄存器映射（地址 0xE0000000 起）：

| 寄存器 | 地址 | 作用 |
|--------|------|------|
| ITM_STIM0-31 | 0xE0000000 + 4*n | 刺激端口（写数据触发输出） |
| ITM_TER | 0xE0000E00 | 刺激端口使能 |
| ITM_TPR | 0xE0000E40 | 刺激端口特权控制 |
| ITM_TCR | 0xE0000E80 | ITM 控制寄存器 |
| ITM_LAR | 0xE0000FB0 | 锁访问密钥（写 0xC5ACCE55 解锁） |

ITM 完整初始化与使用代码：

```c
// ITM initialization for SWO trace output
void itm_init(uint32_t swo_freq) {
    // Enable trace in DEMCR (Debug Exception and Monitor Control)
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    
    // Unlock ITM (write key to LAR)
    ITM->LAR = 0xC5ACCE55;
    
    // Configure ITM trace control
    ITM->TCR = ITM_TCR_TraceBusID_Msk |    // Trace bus ID for routing
               ITM_TCR_SWOENA_Msk    |      // Enable SWO output
               ITM_TCR_SYNCENA_Msk   |      // Enable sync packets
               ITM_TCR_ITMENA_Msk;          // Enable ITM
    
    // Enable stimulus port 0 (for printf-style output)
    ITM->TER |= (1UL << 0);
    
    // Configure SWO prescaler for target baud rate
    // SWO_FREQ = CORE_CLOCK / (SWOSCALAR + 1)
    uint32_t scalar = (SystemCoreClock / swo_freq) - 1;
    TPI->ACPR = scalar;  // Async Clock Prescaler Register
    
    // Set TPI to Manchester/NRZ encoding (NRZ is common)
    TPI->SPPR = 2;       // Protocol: 2 = NRZ (UART-like)
    TPI->FFCR = 0x100;   // Formatter: enable continuous formatting
}

// Write one byte to ITM port 0 (blocking if FIFO full)
void itm_putc(char c) {
    // Wait until stimulus port 0 is ready (FIFO not full)
    while ((ITM->PORT[0] & 1) == 0) {}
    ITM->PORT[0] = (uint32_t)c;  // Write byte triggers SWO output
}

// Printf-style via ITM (safe for ISR use)
void itm_printf(const char *fmt, ...) {
    char buf[128];
    va_list ap;
    va_start(ap, fmt);
    int len = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    for (int i = 0; i < len; i++) {
        itm_putc(buf[i]);
    }
}

// Usage in ISR - does not block CPU
void TIM2_IRQHandler(void) {
    TIM2->SR = ~TIM_SR_UIF;
    itm_printf("[ISR] tick=%lu adc=%d\n", HAL_GetTick(), ADC1->DR);
}
```

ITM 与 UART printf 对比：

| 特性 | UART printf | ITM printf |
|------|-------------|------------|
| 速度 | 受波特率限制（115200 = ~11KB/s） | 高（可达几 MB/s） |
| ISR 安全 | 不安全（可能阻塞） | 安全（FIFO 非阻塞） |
| 引脚占用 | 占用 UART 引脚 | 仅 SWO 引脚 |
| 时间戳 | 无 | 硬件时间戳 |
| 多通道 | 单通道 | 32 端口可分类 |
| 调试器要求 | 无 | 需要 SWO 支持的调试器 |

### 14.4 DWT（Data Watchpoint and Trace）单元

DWT 提供硬件数据断点（Watchpoint）、周期计数器、地址匹配追踪等能力，是性能分析与故障定位的利器。

DWT 寄存器映射（地址 0xE0001000 起）：

| 寄存器 | 地址 | 作用 |
|--------|------|------|
| DWT_CTRL | 0xE0001000 | 控制寄存器（CYCCNTENA 等） |
| DWT_CYCCNT | 0xE0001004 | 周期计数器（32 位） |
| DWT_CPICNT | 0xE0001008 | 多周期指令计数器 |
| DWT_EXCCNT | 0xE000100C | 异常处理耗时计数器 |
| DWT_SLEEPCNT | 0xE0001010 | 睡眠周期计数器 |
| DWT_LSUCNT | 0xE0001014 | Load/Store 单元等待计数器 |
| DWT_FOLDCNT | 0xE0001018 | 折叠指令计数器 |
| DWT_COMP0-3 | 0xE0001020 + 16*n | 比较器参考值 |
| DWT_MASK0-3 | 0xE0001024 + 16*n | 比较器掩码 |
| DWT_FUNCTION0-3 | 0xE0001028 + 16*n | 比较器功能配置 |

#### 14.4.1 周期计数器（CYCCNT）

DWT_CYCCNT 是 32 位自由运行的周期计数器，每个 CPU 时钟周期加 1，是测量中断延迟和 ISR 执行时间的核心工具。

```c
// Initialize DWT cycle counter
void dwt_init(void) {
    // Enable DWT via DEMCR.TRCENA
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    // Reset cycle counter
    DWT->CYCCNT = 0;
    // Enable cycle counter
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
    // Verify enabled
    if ((DWT->CTRL & DWT_CTRL_CYCCNTENA_Msk) == 0) {
        // DWT not supported or locked (some MCUs need unlock)
        log_error("DWT CYCCNT not available");
    }
}

// Measure function execution time in cycles
static inline uint32_t dwt_cycles(void) {
    return DWT->CYCCNT;
}

// Example: measure ISR latency
static volatile uint32_t trigger_cyc;
static volatile uint32_t latency_cyc;

void latency_arm(void) {
    __DSB();
    __ISB();
    trigger_cyc = DWT->CYCCNT;
}

void EXTI0_IRQHandler(void) {
    uint32_t now = DWT->CYCCNT;
    latency_cyc = now - trigger_cyc;  // Cycles since arm
    EXTI->PR = EXTI_LINE_0;
}

// Measure code block time
#define DWT_MEASURE_START()  uint32_t _dwt_s = DWT->CYCCNT
#define DWT_MEASURE_END(var) var = DWT->CYCCNT - _dwt_s

void benchmark(void) {
    DWT_MEASURE_START();
    // ... code to measure ...
    fast_sqrt(x);
    DWT_MEASURE_END(sqrt_cycles);
}
```

#### 14.4.2 DWT 性能计数器组

DWT 还提供 4 个性能分析计数器，可统计 CPU 时间分布：

```c
// Read DWT performance counters for CPU load analysis
typedef struct {
    uint32_t total_cycles;    // DWT_CYCCNT
    uint32_t exc_cycles;      // Time spent in exceptions (ISRs)
    uint32_t sleep_cycles;    // Time in sleep
    uint32_t cpi_cycles;      // Extra cycles for multi-cycle instructions
    uint32_t lsu_cycles;      // Load/store wait cycles
    uint32_t fold_cycles;     // Folded instructions (saved cycles)
} dwt_stats_t;

void dwt_sample(dwt_stats_t *s) {
    s->total_cycles = DWT->CYCCNT;
    s->exc_cycles   = DWT->EXCCNT;
    s->sleep_cycles = DWT->SLEEPCNT;
    s->cpi_cycles   = DWT->CPICNT;
    s->lsu_cycles   = DWT->LSUCNT;
    s->fold_cycles  = DWT->FOLDCNT;
}

// Compute CPU load over a window
float cpu_load_percent(const dwt_stats_t *start, const dwt_stats_t *end) {
    uint32_t total = end->total_cycles - start->total_cycles;
    uint32_t exc   = end->exc_cycles   - start->exc_cycles;
    if (total == 0) return 0.0f;
    return (float)exc * 100.0f / (float)total;
}
```

#### 14.4.3 DWT 地址匹配（Watchpoint）

DWT 的 4 个比较器可设置数据断点，当 CPU 访问匹配地址时触发动作（如记录到 ITM、触发中断、暂停 CPU）：

```c
// Set DWT watchpoint: log all writes to variable g_state
volatile uint32_t g_state;

void dwt_watch_g_state(void) {
    // Configure comparator 0 for data write match
    DWT->COMP0 = (uint32_t)&g_state;       // Address to watch
    DWT->MASK0 = 0;                         // Mask: 0 = exact 32-bit match
    // FUNCTION: 0b0100 = data write, emit ITM trace packet
    DWT->FUNCTION0 = (0b0100 << DWT_FUNCTION_FUNCTION_Pos) |
                     (0b10 << DWT_FUNCTION_SIZE_Pos);  // 0b10 = 32-bit
}

// Watchpoint will now emit ITM packet each time g_state is written
// Debugger (e.g., SystemView) shows who wrote the variable and when
```

DWT 比较器功能模式：

| FUNCTION 值 | 模式 | 触发动作 |
|-------------|------|---------|
| 0b0000 | 禁用 | 无 |
| 0b0100 | 数据写入匹配 | 输出 ITM 数据包 |
| 0b0101 | 数据读取匹配 | 输出 ITM 数据包 |
| 0b0110 | 数据读/写匹配 | 输出 ITM 数据包 |
| 0b1000 | PC 匹配 | 输出 ITM PC 采样 |
| 0b1100 | 数据写入匹配 | 暂停 CPU（断点） |
| 0b1101 | 数据读取匹配 | 暂停 CPU |
| 0b1110 | 数据读/写匹配 | 暂停 CPU |

### 14.5 ETM（Embedded Trace Macrocell）指令追踪

ETM 提供指令级追踪（记录每条执行的指令），是比 ITM 更底层的追踪单元。ETM 数据量大，需要专用追踪引脚（并行 4/8/16 位或串行 SWO）。

ETM 主要用途：
- **代码覆盖率分析**：确认测试覆盖了多少代码路径。
- **故障后回溯**：记录崩溃前执行的指令序列。
- **性能热点定位**：统计每段代码的执行次数。

ETM 仅在 Cortex-M3/M4/M7（高端型号）上可用，且需芯片厂商实现追踪引脚。STM32F4 的 ETM 可配置为 4 位并行追踪（ETMTRACECLK + ETMTRACE[3:0]）。

```c
// Enable ETM instruction trace (simplified)
void etm_init(void) {
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    
    // Unlock ETM (write key)
    ETM->LAR = 0xC5ACCE55;
    
    // Configure ETM control
    ETM->CR = ETM_CR_POWERDOWN_Msk;  // Power down first
    ETM->CR = 0;                      // Power up
    ETM->CR = ETM_CR_PROGRAM_Msk;     // Enter programming mode
    
    // Configure trace start/stop resources
    ETM->TRACEIDR = 0x01;  // Trace ID (must be non-zero)
    
    // Enable viewdata resources (trace all instructions)
    ETM->CR = ETM_CR_PORTSIZE_0 |     // 4-bit port
              ETM_CR_STALL_PROCESSOR; // Stall CPU if FIFO full
    
    // Exit programming mode
    ETM->CR &= ~ETM_CR_PROGRAM_Msk;
}
```

由于 ETM 数据量极大（每秒可达 GB 级），实际开发中较少持续使用，多用于离线分析。SEGGER 的 Ozone 调试器支持 ETM 回溯，可在崩溃后查看最近的指令流。

### 14.6 MTB（Micro Trace Buffer）用于 Cortex-M0+

Cortex-M0/M0+ 没有完整的 ETM/ITM，但提供了 MTB（Micro Trace Buffer）作为轻量级指令追踪方案。MTB 使用一块 SRAM 作为环形缓冲区，记录分支指令的源地址和目标地址，调试器据此重建执行流。

```c
// MTB configuration for Cortex-M0+
// MTB uses a region of SRAM as circular trace buffer
#define MTB_BUFFER_SIZE  1024  // In words (4096 bytes)

// Place MTB buffer in dedicated SRAM section
__attribute__((section(".mtb_buffer")))
static volatile uint32_t mtb_buf[MTB_BUFFER_SIZE];

void mtb_init(void) {
    // Set MTB position register (buffer start address)
    MTB->POSITION = (uint32_t)mtb_buf;
    // Set flow register: watermark for auto-stop
    MTB->FLOW = ((uint32_t)mtb_buf + sizeof(mtb_buf) - 16) |
                MTB_FLOW_WATERMARK_Msk;
    // Set master enable
    MTB->MASTER = MTB_MASTER_EN_Msk |
                  (MTB_BUFFER_SIZE << MTB_MASTER_MASK_Pos);
}

// MTB records branches automatically, no code needed in ISR
// Debugger reads mtb_buf and reconstructs execution path
```

MTB 的优势在于仅占用少量 SRAM（几 KB）即可实现"崩溃前回溯"，非常适合资源受限的 M0+ 系统。NXP 的 Kinetis 系列广泛使用 MTB。

### 14.7 调试器配置实战

#### 14.7.1 OpenOCD 配置

OpenOCD 是开源调试器，支持 ST-Link、J-Link、CMSIS-DAP 等。配置文件示例：

```
# openocd_stm32f4.cfg - OpenOCD config for STM32F4 via ST-Link
source [find interface/stlink.cfg]
transport select hla_swd

# STM32F4 target
set CHIPNAME stm32f4
source [find target/stm32f4x.cfg]

# SWD clock speed (adapt to target clock)
adapter_khz 4000

# Reset configuration
reset_config srst_only srst_nogate

# Enable SWO trace at 2MHz
tpiu config internal :swodump.txt uart 2000000
itm port 0 on

# Init script
init
reset init
halt
```

启动调试：
```
openocd -f openocd_stm32f4.cfg
# In another terminal
arm-none-eabi-gdb firmware.elf
(gdb) target remote :3333
(gdb) load
(gdb) continue
```

#### 14.7.2 ST-Link 配置（STM32CubeIDE）

STM32CubeIDE 内置 ST-Link 配置，关键设置：

- **Debug probe**: ST-LINK GDB server
- **SWD frequency**: 4 MHz（默认）/ 24 MHz（高速）
- **Reset mode**: Hardware reset (NRST)
- **Trace**: Enable SWO at 2000 kHz（用于 ITM）
- **Flash download**: Enable, verify

#### 14.7.3 J-Link 配置

SEGGER J-Link 配置（J-Link Commander 或 GDB Server）：

```
# JLinkGDBServerCL command line
JLinkGDBServerCL -device STM32F407VG -if SWD -speed 4000 -port 2331

# J-Link Commander for SWO trace
JLinkExe -device STM32F407VG -if SWD -speed 4000
J-Link> SWO View 0       # View ITM port 0
J-Link> SWO Enable 2000  # Enable SWO at 2 MHz
```

J-Link 的优势：SWO 解析能力强，支持 SystemView 实时分析，速度最快（可达 50 MHz SWD）。

### 14.8 调试器对中断的影响

调试器介入会显著影响中断时序，需特别注意：

| 调试操作 | 影响 | 建议 |
|---------|------|------|
| 断点暂停 | CPU 停止，外设继续运行 | 中断标志可能堆积，恢复后批量触发 |
| 单步执行 | 每步检查断点 | SysTick 周期变化，HAL_GetTick 不准 |
| Watchpoint | 数据访问时暂停 | 同断点 |
| 实时模式（Real-time） | 调试器不断点 CPU | 推荐，仅读内存/寄存器 |
| ITM 追踪 | 几乎无影响（SWO 异步） | 推荐用于实时分析 |

```c
// Detect if debugger is attached (to skip timing-sensitive debug code)
bool debugger_attached(void) {
    return (CoreDebug->DHCSR & CoreDebug_DHCSR_C_DEBUGEN_Msk) != 0;
}

void critical_loop(void) {
    if (debugger_attached()) {
        // Skip strict timing asserts when debugging
    } else {
        // Production mode: enforce timing
        assert(latency < MAX_LATENCY);
    }
}
```

### 14.9 调试与追踪系统选型指南

| 需求 | 推荐组件 | 说明 |
|------|---------|------|
| printf 调试日志 | ITM + SWO | 非 ISR 阻塞，可分类 |
| 测量中断延迟 | DWT CYCCNT | 周期精确 |
| 监控变量写入 | DWT Watchpoint | 硬件断点 |
| CPU 占用率分析 | DWT EXCCNT/SLEEPCNT | 自动统计 |
| 故障后回溯 | ETM 或 MTB | 指令流重建 |
| 实时可视化 | SEGGER SystemView | 综合方案 |
| 代码覆盖率 | ETM | 离线分析 |
| M0+ 追踪 | MTB | 唯一选择 |

---

## 15. RTOS 中断设计模式

RTOS 下的中断设计与裸机有本质差异：中断不仅是事件响应入口，还承担任务唤醒、任务间通信、调度触发等职责。本章总结 RTOS 中断的核心设计模式，涵盖临界区实现、优先级分配、ISR-任务通信、优先级翻转处理，并给出完整的 UART 接收驱动示例。

### 15.1 FreeRTOS 临界区实现：BASEPRI vs PRIMASK

FreeRTOS 在 Cortex-M 上提供两种临界区实现，开发者需理解其差异才能正确选择。

**PRIMASK 方式**：屏蔽所有可屏蔽中断（除 NMI/HardFault），最简单但最粗暴：

```c
// PRIMASK-based critical section (blocks ALL interrupts)
void critical_section_primask(void) {
    uint32_t primask_save = __get_PRIMASK();
    __disable_irq();       // Set PRIMASK=1, block all IRQs
    // ... atomic operation ...
    __set_PRIMASK(primask_save);  // Restore
}
```

**BASEPRI 方式**：仅屏蔽优先级数值 ≥ 阈值的中断，保留高优先级中断实时性：

```c
// BASEPRI-based critical section (RTOS-friendly)
// Blocks only IRQs with priority >= threshold
void critical_section_basepri(void) {
    uint32_t basepri_save = __get_BASEPRI();
    // Set threshold: block IRQs with priority value >= 5
    // (priority 0-4 still active, e.g., motor control)
    __set_BASEPRI(5 << (8 - __NVIC_PRIO_BITS));
    // ... atomic operation ...
    __set_BASEPRI(basepri_save);  // Restore (0 = no masking)
}
```

FreeRTOS 默认使用 BASEPRI（通过 `configMAX_SYSCALL_INTERRUPT_PRIORITY` 阈值）。两种方式对比：

| 特性 | PRIMASK | BASEPRI |
|------|---------|---------|
| 屏蔽范围 | 所有 IRQ | 仅 ≥ 阈值的 IRQ |
| 高优先级中断 | 被屏蔽 | 保留响应 |
| RTOS 适用 | 不推荐 | 推荐（默认） |
| 嵌套支持 | 不支持（计数需手动） | 支持（FreeRTOS 内部计数） |
| 代码量 | 最少 | 略多 |

FreeRTOS 临界区宏的实现（Cortex-M 端口）：

```c
// FreeRTOS portmacro.h (Cortex-M)
#define portDISABLE_INTERRUPTS() \
    __asm volatile(" msr basepri, %0" :: "r"(configMAX_SYSCALL_INTERRUPT_PRIORITY))

#define portENABLE_INTERRUPTS() \
    __asm volatile(" msr basepri, %0" :: "r"(0))

// Reentrant critical section (with nesting count)
#define portENTER_CRITICAL()  vPortEnterCritical()
#define portEXIT_CRITICAL()   vPortExitCritical()

// Implementation in port.c
static UBaseType_t uxCriticalNesting = 0xAAAAAAAA;
void vPortEnterCritical(void) {
    portDISABLE_INTERRUPTS();
    uxCriticalNesting++;
    if (uxCriticalNesting == 1) {
        // First entry: check we're not already in critical
        configASSERT((SCB->ICSR & SCB_ICSR_VECTACTIVE_Msk) == 0);
    }
}
void vPortExitCritical(void) {
    configASSERT(uxCriticalNesting > 0);
    uxCriticalNesting--;
    if (uxCriticalNesting == 0) {
        portENABLE_INTERRUPTS();
    }
}
```

### 15.2 RTOS 中断优先级分配策略

RTOS 中断优先级分配是系统设计的关键。核心概念是 **SYSCALL_PRIORITY**（系统调用优先级阈值）：优先级数值小于此阈值的中断不能调用 RTOS API，保证它们能立即抢占 RTOS 内核操作。

优先级分配模型（以 STM32F4，4 位优先级，MAX_SYSCALL=5 为例）：

| 优先级范围 | 调用 RTOS API | 用途 | 示例 |
|-----------|--------------|------|------|
| 0 | 禁止 | 硬实时控制 | 电机 PWM、电流环 |
| 1 | 禁止 | 硬实时采样 | 高速 ADC |
| 2 | 禁止 | 紧急故障 | 过流保护 |
| 3-4 | 禁止 | 关键外设 | 高速 CAN |
| 5-15 | 允许 | 普通外设、通信 | UART/SPI/按钮 |

```c
// Complete RTOS-aware priority assignment
void rtos_irq_priority_init(void) {
    // Set priority grouping (4 bits preemption, 0 sub)
    HAL_NVIC_SetPriorityGrouping(NVIC_PRIORITYGROUP_4);
    
    // === No RTOS API allowed (priority 0-4) ===
    // These CAN preempt RTOS kernel critical sections
    HAL_NVIC_SetPriority(TIM1_UP_TIM10_IRQn, 0, 0);  // Motor current loop
    HAL_NVIC_SetPriority(ADC_IRQn,            1, 0);  // High-speed sampling
    HAL_NVIC_SetPriority(COMP_IRQn,           2, 0);  // Overcurrent comparator
    
    // === RTOS API allowed (priority 5-15) ===
    HAL_NVIC_SetPriority(DMA1_Stream0_IRQn,   5, 0);  // DMA w/ xQueueGiveFromISR
    HAL_NVIC_SetPriority(USART1_IRQn,         6, 0);  // UART RX
    HAL_NVIC_SetPriority(SPI1_IRQn,           7, 0);  // SPI
    HAL_NVIC_SetPriority(CAN1_RX0_IRQn,       8, 0);  // CAN receive
    HAL_NVIC_SetPriority(TIM3_IRQn,           9, 0);  // General timer
    HAL_NVIC_SetPriority(EXTI0_IRQn,         12, 0);  // Button
    HAL_NVIC_SetPriority(EXTI15_10_IRQn,     13, 0);  // Multiple EXTI
    
    // === RTOS kernel (lowest priority, mandatory) ===
    // PendSV and SysTick MUST be at configLIBRARY_LOWEST_INTERRUPT_PRIORITY
}
```

SYSCALL_PRIORITY 设计准则：
1. 阈值不能太高（数值太小），否则太多中断被禁 RTOS API。
2. 阈值不能太低（数值太大），否则 RTOS 临界区可被普通中断抢占，破坏原子性。
3. 推荐值：4 位优先级用 5（保留 0-4 给硬实时），3 位优先级用 3。

### 15.3 从 ISR 唤醒任务

ISR 唤醒任务是 RTOS 中断最常见的模式。FreeRTOS 提供多种 FromISR API：

| API | 用途 | 适用场景 |
|-----|------|---------|
| xTaskNotifyFromISR | 直接任务通知（最轻量） | 1对1通知 |
| xSemaphoreGiveFromISR | 二值信号量 | ISR-任务同步 |
| xQueueSendFromISR | 队列发送 | 数据传递 |
| xEventGroupSetBitsFromISR | 事件组 | 多事件等待 |
| vTaskNotifyGiveFromISR | 通知+计数值 | 简化通知 |

#### 15.3.1 xTaskNotifyFromISR 示例

```c
// Direct task notification - most efficient ISR-to-task signaling
static TaskHandle_t s_adc_task_handle;

// ISR: ADC conversion complete, notify task
void ADC_IRQHandler(void) {
    BaseType_t higher_priority_task_woken = pdFALSE;
    if (ADC1->SR & ADC_SR_EOC) {
        uint16_t value = ADC1->DR;
        // Send value as notification value (32-bit)
        vTaskNotifyGiveFromISR(s_adc_task_handle, &higher_priority_task_woken);
        // OR: send specific value
        // xTaskNotifyFromISR(s_adc_task_handle, value, eSetValueWithOverwrite,
        //                    &higher_priority_task_woken);
    }
    // Yield if woken task has higher priority than current
    portYIELD_FROM_ISR(higher_priority_task_woken);
}

// Task: waits for notification, processes ADC
void adc_task(void *pv) {
    uint32_t notify_value;
    for (;;) {
        // Block until notified (clears count on exit)
        if (xTaskNotifyWait(0, 0xFFFFFFFF, &notify_value, portMAX_DELAY) == pdTRUE) {
            uint16_t adc_val = (uint16_t)notify_value;
            process_adc(adc_val);
        }
    }
}
```

#### 15.3.2 xSemaphoreGiveFromISR 示例

```c
// Binary semaphore for ISR-to-task sync
static SemaphoreHandle_t s_exti_sem;

void exti_init(void) {
    s_exti_sem = xSemaphoreCreateBinary();
    // Configure EXTI0 (button)
    // ...
    HAL_NVIC_SetPriority(EXTI0_IRQn, 6, 0);  // >= MAX_SYSCALL
    HAL_NVIC_EnableIRQ(EXTI0_IRQn);
}

void EXTI0_IRQHandler(void) {
    BaseType_t hpw = pdFALSE;
    EXTI->PR = EXTI_LINE_0;  // Clear flag FIRST
    xSemaphoreGiveFromISR(s_exti_sem, &hpw);
    portYIELD_FROM_ISR(hpw);
}

void button_task(void *pv) {
    for (;;) {
        // Block until ISR gives semaphore
        xSemaphoreTake(s_exti_sem, portMAX_DELAY);
        handle_button_press();
    }
}
```

### 15.4 中断安全队列（xQueueSendFromISR）

队列是 RTOS 中断传递数据的标准方式。完整 UART 接收队列示例：

```c
// UART RX via interrupt + FreeRTOS queue
#define UART_RX_QUEUE_LEN  128
static QueueHandle_t s_uart_rx_queue;
static TaskHandle_t   s_uart_parser_task;

void uart_rx_init(void) {
    // Create queue for received bytes
    s_uart_rx_queue = xQueueCreate(UART_RX_QUEUE_LEN, sizeof(uint8_t));
    // Configure USART1
    USART1->CR1 |= USART_CR1_RXNEIE;  // Enable RXNE interrupt
    HAL_NVIC_SetPriority(USART1_IRQn, 6, 0);
    HAL_NVIC_EnableIRQ(USART1_IRQn);
}

void USART1_IRQHandler(void) {
    BaseType_t hpw = pdFALSE;
    uint32_t sr = USART1->SR;
    
    if (sr & USART_SR_RXNE) {
        uint8_t byte = USART1->DR;  // Read clears RXNE
        // Send byte to queue (non-blocking from ISR)
        xQueueSendFromISR(s_uart_rx_queue, &byte, &hpw);
    }
    if (sr & USART_SR_ORE) {
        (void)USART1->DR;  // Clear overrun
        // Notify parser task of overrun
        xTaskNotifyFromISR(s_uart_parser_task, 0x01, eSetBits, &hpw);
    }
    // Yield to higher-priority woken task
    portYIELD_FROM_ISR(hpw);
}

// Parser task: assembles bytes into frames
void uart_parser_task(void *pv) {
    uint8_t byte;
    uint8_t frame[64];
    uint8_t idx = 0;
    for (;;) {
        // Wait for byte (timeout 100ms to handle partial frames)
        if (xQueueReceive(s_uart_rx_queue, &byte, pdMS_TO_TICKS(100)) == pdTRUE) {
            frame[idx++] = byte;
            if (byte == '\n' || idx >= sizeof(frame)) {
                frame[idx] = '\0';
                process_uart_frame(frame, idx);
                idx = 0;
            }
        } else {
            // Timeout: discard partial frame
            idx = 0;
        }
    }
}
```

队列使用要点：
1. `xQueueSendFromISR` 的 `pxHigherPriorityTaskWoken` 参数必须检查并调用 `portYIELD_FROM_ISR`。
2. 队列满时 `xQueueSendFromISR` 返回 `errQUEUE_FULL`，需处理（如丢弃或统计）。
3. 不要在 ISR 中调用 `xQueueReceive`（无 FromISR 版本用于接收场景罕见）。

### 15.5 优先级翻转与优先级继承

优先级翻转是 RTOS 多任务共享资源的经典问题：

```
低优先级任务 T_low 持有资源锁
→ 高优先级任务 T_high 等待锁
→ 中优先级任务 T_mid 抢占 T_low
→ T_high 被 T_mid 间接阻塞（违背优先级语义）
```

FreeRTOS 通过**优先级继承互斥量**（Priority Inheritance Mutex）缓解：

```c
// Priority inheritance mutex (NOT binary semaphore!)
SemaphoreHandle_t s_resource_mutex;

void resource_init(void) {
    // xSemaphoreCreateMutex enables priority inheritance
    s_resource_mutex = xSemaphoreCreateMutex();
}

void high_prio_task(void *pv) {
    for (;;) {
        xSemaphoreTake(s_resource_mutex, portMAX_DELAY);
        // If low task holds mutex, RTOS temporarily raises low task's
        // priority to THIS task's priority, blocking T_mid from preempting
        access_shared_resource();
        xSemaphoreGive(s_resource_mutex);
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

void low_prio_task(void *pv) {
    for (;;) {
        xSemaphoreTake(s_resource_mutex, portMAX_DELAY);
        // While holding mutex, my priority may be raised to T_high's level
        do_slow_resource_work();
        xSemaphoreGive(s_resource_mutex);  // Priority restored
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}
```

互斥量 vs 二值信号量对比：

| 特性 | 二值信号量 | 互斥量 |
|------|-----------|--------|
| 优先级继承 | 无 | 有 |
| 适用场景 | ISR-任务同步 | 任务间资源共享 |
| 可在 ISR 中 Give | 可以 | 不可以（Give 没有阻塞版本） |
| 嵌套获取 | 不支持 | 支持（同任务可多次 Take） |
| 内存开销 | 较小 | 略大（记录所有者） |

### 15.6 完整 RTOS 中断驱动示例：UART 接收 + 任务处理

本节给出一个生产级的 UART 接收驱动，结合 DMA、空闲中断、任务通知，实现高效可靠的串口接收：

```c
// Production UART driver: DMA + IDLE interrupt + task notification
#include "FreeRTOS.h"
#include "task.h"
#include "stream_buffer.h"

#define UART_RX_BUF_SIZE   256
#define UART_STREAM_SIZE   512

static StreamBufferHandle_t s_rx_stream;
static TaskHandle_t s_rx_task_handle;
static uint8_t s_dma_buf[UART_RX_BUF_SIZE];

// Initialize UART with DMA circular receive
void uart_rx_dma_init(void) {
    // Create stream buffer (better than queue for byte streams)
    s_rx_stream = xStreamBufferCreate(UART_STREAM_SIZE, 1);
    
    // Configure USART1 RX with DMA
    USART1->CR3 |= USART_CR3_DMAR;  // Enable DMA for RX
    DMA2_Stream2->PAR = (uint32_t)&USART1->DR;
    DMA2_Stream2->M0AR = (uint32_t)s_dma_buf;
    DMA2_Stream2->NDTR = UART_RX_BUF_SIZE;
    DMA2_Stream2->CR = DMA_SxCR_CHSEL_4 |    // Channel 4 for USART1
                       DMA_SxCR_MINC |        // Memory increment
                       DMA_SxCR_PSIZE_0 |     // 8-bit peripheral
                       DMA_SxCR_MSIZE_0 |     // 8-bit memory
                       DMA_SxCR_CIRC |        // Circular mode
                       DMA_SxCR_HTIE |        // Half-transfer interrupt
                       DMA_SxCR_TCIE |        // Transfer-complete interrupt
                       DMA_SxCR_EN;
    
    // Enable UART IDLE interrupt (fires on RX line idle = frame end)
    USART1->CR1 |= USART_CR1_IDLEIE;
    
    HAL_NVIC_SetPriority(USART1_IRQn, 6, 0);
    HAL_NVIC_SetPriority(DMA2_Stream2_IRQn, 6, 0);
    HAL_NVIC_EnableIRQ(USART1_IRQn);
    HAL_NVIC_EnableIRQ(DMA2_Stream2_IRQn);
}

// DMA half-transfer: first half of buffer ready
void DMA2_Stream2_IRQHandler(void) {
    BaseType_t hpw = pdFALSE;
    if (DMA2->LISR & DMA_LISR_HTIF2) {
        DMA2->LIFCR = DMA_LIFCR_CHTIF2;
        // Send first half [0..127] to stream
        xStreamBufferSendFromISR(s_rx_stream, s_dma_buf,
                                 UART_RX_BUF_SIZE / 2, &hpw);
    }
    if (DMA2->LISR & DMA_LISR_TCIF2) {
        DMA2->LIFCR = DMA_LIFCR_CTCIF2;
        // Send second half [128..255] to stream
        xStreamBufferSendFromISR(s_rx_stream, s_dma_buf + UART_RX_BUF_SIZE / 2,
                                 UART_RX_BUF_SIZE / 2, &hpw);
    }
    portYIELD_FROM_ISR(hpw);
}

// UART IDLE: frame boundary detected, send remaining bytes
void USART1_IRQHandler(void) {
    BaseType_t hpw = pdFALSE;
    if (USART1->SR & USART_SR_IDLE) {
        (void)USART1->DR;  // Clear IDLE
        // Calculate bytes received since last event
        uint16_t remaining = DMA2_Stream2->NDTR;
        uint16_t bytes_in_buf = UART_RX_BUF_SIZE - remaining;
        uint16_t offset = (remaining == 0) ? 0 : (UART_RX_BUF_SIZE - remaining);
        // Send partial buffer
        if (bytes_in_buf > 0) {
            xStreamBufferSendFromISR(s_rx_stream, s_dma_buf + offset,
                                     bytes_in_buf, &hpw);
        }
        // Notify task that frame may be complete
        vTaskNotifyGiveFromISR(s_rx_task_handle, &hpw);
    }
    portYIELD_FROM_ISR(hpw);
}

// RX processing task
void uart_rx_task(void *pv) {
    uint8_t frame[256];
    size_t frame_len;
    for (;;) {
        // Wait for notification (IDLE detected)
        xTaskNotifyWait(0, 0xFFFFFFFF, NULL, pdMS_TO_TICKS(50));
        // Drain stream buffer
        frame_len = xStreamBufferReceive(s_rx_stream, frame,
                                         sizeof(frame), 0);
        if (frame_len > 0) {
            process_uart_frame(frame, frame_len);
        }
    }
}
```

该驱动特点：
- DMA 循环接收，零 CPU 开销搬运数据。
- 半传输/完成中断保证流式数据不丢失。
- IDLE 中断标记帧边界，适合变长协议。
- Stream Buffer 比队列更适合字节流。

---

## 16. 低功耗中断设计

低功耗是电池供电 IoT 设备的核心需求。Cortex-M 提供多种低功耗模式，每种模式的唤醒源、唤醒延迟、功耗不同。本章详解 WFI/WFE 指令、Sleep/Stop/Standby 模式的中断唤醒，并给出 RTC 闹钟唤醒、EXTI 唤醒、LPTIM 唤醒的完整代码。

### 16.1 WFI/WFE 指令详解

Cortex-M 提供两条进入低功耗的指令：

| 指令 | 全称 | 唤醒条件 | 用途 |
|------|------|---------|------|
| WFI | Wait For Interrupt | 任意中断（含 NMI） | 标准低功耗入口 |
| WFE | Wait For Event | 事件（SEV 指令或中断） | 多核/自旋等待 |
| SEV | Send Event | 唤醒等待 WFE 的核 | 多核同步 |

WFI 与 WFE 的关键差异：
- WFI 唤醒后**会执行** ISR（中断被服务）。
- WFE 唤醒后**不执行** ISR（仅退出等待状态，因为事件被消费了）。
- WFE 有内部事件锁存器：如果之前有未消费事件，WFE 立即返回。

```c
// WFI: standard sleep, wakes on any interrupt
void sleep_wfi(void) {
    __WFI();  // CPU halts here, resumes after ISR completes
    // Code here runs AFTER ISR has executed
}

// WFE: event-based wait, does NOT run ISR on wake
void sleep_wfe(void) {
    __WFE();  // CPU halts, resumes on next event WITHOUT ISR
    // If an interrupt was pending, it fires next instruction
}

// SEV: send event to all cores (and local)
void wake_other_core(void) {
    __SEV();  // Wakes core waiting in WFE
}

// Multi-core spin-wait pattern (avoid WFI in tight loops)
void spin_wait_event(volatile uint32_t *flag) {
    while (*flag == 0) {
        __WFE();  // Low power wait
    }
    *flag = 0;
}
```

SCR（System Control Register）控制睡眠行为：

| 位 | 名称 | 作用 |
|----|------|------|
| 0 | SLEEPONEXIT | ISR 退出后自动睡眠（无任务时节能） |
| 1 | SLEEPDEEP | 进入深度睡眠（Stop/Standby） |
| 2 | SLEEPONPEND | 中断挂起时唤醒（即使禁用） |

```c
// Configure sleep behavior
void configure_sleep(SleepMode mode) {
    uint32_t scr = SCB->SCR;
    if (mode == SLEEP_ON_EXIT) {
        scr |= SCB_SCR_SLEEPONEXIT_Msk;  // Auto-sleep after ISR
    }
    if (mode == SLEEPDEEP) {
        scr |= SCB_SCR_SLEEPDEEP_Msk;    // Enter Stop/Standby
    }
    if (mode == WAKE_ON_PEND) {
        scr |= SCB_SCR_SEVONPEND_Msk;    // Pending IRQ wakes WFE
    }
    SCB->SCR = scr;
    __DSB();
    __ISB();
}
```

### 16.2 Sleep/Stop/Standby 模式对比

STM32 提供三种低功耗模式，功耗与唤醒延迟递增：

| 模式 | 功耗 | 唤醒延迟 | 唤醒源 | RAM 保持 | 寄存器保持 |
|------|------|---------|--------|---------|-----------|
| Sleep | ~几 mA | 6+ cycles | 任意中断 | 是 | 是 |
| Stop | ~几十 µA | ~10 µs | EXTI/RTC | 是 | 是 |
| Standby | ~2 µA | ~ms（重启） | WKUP/RTC | 否 | 否 |

- **Sleep**：仅停 CPU 时钟，外设继续运行。适合短暂空闲。
- **Stop**：停所有时钟（保留 LSI/LSE），保留 RAM。EXTI/RTC 唤醒。
- **Standby**：断电 RAM，仅备份域运行。唤醒等同于复位。

### 16.3 RTC 闹钟唤醒完整代码

RTC 闹钟是周期性低功耗唤醒的标准方案（如每 10 秒采集一次传感器）：

```c
// RTC alarm wakeup from Stop mode (every 10 seconds)
void rtc_alarm_wakeup_init(void) {
    // Enable LSE (low-speed external crystal, 32.768kHz)
    RCC->BDCR |= RCC_BDCR_LSEON;
    while (!(RCC->BDCR & RCC_BDCR_LSERDY)) {}
    
    // Select LSE as RTC clock source
    RCC->BDCR |= RCC_BDCR_RTCSEL_0;
    // Enable RTC clock
    RCC->BDCR |= RCC_BDCR_RTCEN;
    
    // Unlock RTC write protection
    RTC->WPR = 0xCA;
    RTC->WPR = 0x53;
    
    // Enter configuration mode
    RTC->ISR |= RTC_ISR_INIT;
    while (!(RTC->ISR & RTC_ISR_INITF)) {}
    
    // Set prescaler for 1Hz clock (LSE=32768Hz)
    RTC->PRER = (0x7F << 16) | 0xFF;  // Async=127, Sync=255
    
    // Exit configuration mode
    RTC->ISR &= ~RTC_ISR_INIT;
    
    // Configure Alarm A for 10-second periodic wakeup
    RTC->CR &= ~RTC_CR_ALRAE;  // Disable alarm first
    while (!(RTC->ISR & RTC_ISR_ALRAWF)) {}  // Wait for write allow
    
    // Set alarm mask: only seconds field matters, trigger every 10s
    RTC->ALRMAR = RTC_ALRMAR_MSK4 |    // Mask date
                  RTC_ALRMAR_MSK3 |    // Mask hours
                  RTC_ALRMAR_MSK2 |    // Mask minutes
                  (10 << RTC_ALRMAR_SU_Pos);  // Seconds = 10
    
    // Enable alarm interrupt
    RTC->CR |= RTC_CR_ALRAIE | RTC_CR_ALRAE;
    
    // Configure EXTI line 17 for RTC alarm wakeup
    EXTI->IMR  |= EXTI_IMR_MR17;
    EXTI->EMR  |= EXTI_EMR_MR17;
    EXTI->RTSR |= EXTI_RTSR_TR17;  // Rising edge
    
    // Enable RTC alarm IRQ in NVIC
    HAL_NVIC_SetPriority(RTC_Alarm_IRQn, 5, 0);
    HAL_NVIC_EnableIRQ(RTC_Alarm_IRQn);
    
    // Lock RTC write protection
    RTC->WPR = 0xFF;
}

// RTC alarm ISR
void RTC_Alarm_IRQHandler(void) {
    if (RTC->ISR & RTC_ISR_ALRAF) {
        RTC->ISR &= ~RTC_ISR_ALRAF;  // Clear alarm flag
        EXTI->PR = EXTI_PR_PR17;      // Clear EXTI pending
        // Wakeup event: read sensor, send data, go back to sleep
        sensor_read_and_send();
    }
}

// Main loop: enter Stop mode, wake on RTC alarm
void low_power_main_loop(void) {
    while (1) {
        // Do work (sensor read, data send)
        do_periodic_work();
        
        // Enter Stop mode (wakes on next RTC alarm)
        HAL_PWR_EnterSTOPMode(PWR_LOWPOWERREGULATOR_ON, PWR_STOPENTRY_WFI);
        
        // After wakeup, reconfigure system clock (Stop mode stops PLL)
        SystemClock_Config();
    }
}
```

### 16.4 外部中断唤醒（EXTI 配置）

EXTI 唤醒用于按键、传感器信号等外部事件触发的低功耗设备：

```c
// Configure PA0 (button) to wake from Stop mode
void exti_wakeup_init(void) {
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_SYSCFG_CLK_ENABLE();
    
    GPIO_InitTypeDef gi = {0};
    gi.Pin = GPIO_PIN_0;
    gi.Mode = GPIO_MODE_IT_FALLING;  // Falling edge (button press)
    gi.Pull = GPIO_NOPULL;           // External pull-up
    HAL_GPIO_Init(GPIOA, &gi);
    
    // Map PA0 to EXTI line 0
    SYSCFG->EXTICR[0] = SYSCFG_EXTICR1_EXTI0_PA;
    
    // Configure EXTI for wakeup (must be in IMR to wake from Stop)
    EXTI->IMR  |= EXTI_IMR_MR0;
    EXTI->EMR  |= EXTI_EMR_MR0;   // Event mode also wakes
    EXTI->FTSR |= EXTI_FTSR_TR0;  // Falling edge trigger
    
    HAL_NVIC_SetPriority(EXTI0_IRQn, 6, 0);
    HAL_NVIC_EnableIRQ(EXTI0_IRQn);
}

void EXTI0_IRQHandler(void) {
    EXTI->PR = EXTI_LINE_0;  // Clear pending
    button_pressed_flag = 1;
}
```

EXTI 唤醒 Stop 模式的要点：
1. EXTI 必须在 IMR（中断屏蔽）或 EMR（事件屏蔽）中使能。
2. 唤醒后 EXTI 挂起位置位，需在 ISR 中清除。
3. Stop 模式下 GPIO 时钟停止，但 EXTI 检测电路保持运行（由 LSI/LSE 供电）。

### 16.5 低功耗定时器（LPTIM）

LPTIM 是 STM32 的低功耗定时器，可在 Stop 模式下运行（由 LSI/LSE 供电），用于周期性唤醒而无需 RTC 复杂配置：

```c
// LPTIM1 periodic wakeup (1Hz) from Stop mode
void lptim_wakeup_init(void) {
    // Enable LSI (low-speed internal, ~32kHz)
    RCC->CSR |= RCC_CSR_LSION;
    while (!(RCC->CSR & RCC_CSR_LSIRDY)) {}
    
    // Select LSI as LPTIM1 clock source
    RCC->CCIPR |= RCC_CCIPR_LPTIM1SEL_0;  // 01 = LSI
    
    // Enable LPTIM1 clock
    __HAL_RCC_LPTIM1_CLK_ENABLE();
    
    // Configure LPTIM1
    LPTIM1->CFGR = (0 << LPTIM_CFGR_PRESC_Pos) |    // Prescaler /1
                   (0 << LPTIM_CFGR_TRIGEN_Pos) |    // Software trigger
                   LPTIM_CFGR_TIMOUT;                 // Timeout enabled
    
    // Enable LPTIM
    LPTIM1->CR = LPTIM_CR_ENABLE;
    
    // Set compare for 1Hz (LSI ~32kHz, /1 = 32000 cycles)
    LPTIM1->CMP = 32000;
    LPTIM1->ARR = 0xFFFF;  // Auto-reload max
    
    // Enable compare match interrupt
    LPTIM1->IER = LPT_IER_CMPMIE;
    
    HAL_NVIC_SetPriority(LPTIM1_IRQn, 5, 0);
    HAL_NVIC_EnableIRQ(LPTIM1_IRQn);
    
    // Start in continuous mode
    LPTIM1->CR |= LPTIM_CR_CNTSTRT;
}

void LPTIM1_IRQHandler(void) {
    if (LPTIM1->ISR & LPTIM_ISR_CMPM) {
        LPTIM1->ICR = LPTIM_ICR_CMPMCF;  // Clear flag
        periodic_wakeup_handler();
    }
}
```

LPTIM vs RTC 选型：

| 特性 | RTC | LPTIM |
|------|-----|-------|
| 时钟源 | LSE（精确） | LSI/LSE |
| 最小周期 | 1 秒 | 毫秒级 |
| 复杂度 | 高（日历） | 低（计数器） |
| 唤醒精度 | 高（LSE） | 中（LSI ±5%） |
| 适用 | 周期秒级 | 周期毫秒级 |

### 16.6 功耗测量与优化代码

精确测量各模式功耗，定位优化点：

```c
// Power consumption profiler
typedef struct {
    uint32_t sleep_count;
    uint32_t stop_count;
    uint32_t standby_count;
    uint32_t active_cycles;     // DWT cycles in active state
    uint32_t sleep_cycles;      // DWT SLEEPCNT
    uint32_t wakeup_latency_us; // Last wakeup latency
} power_stats_t;

static power_stats_t g_power;

// Measure wakeup latency using DWT
void enter_stop_with_measurement(void) {
    uint32_t before = DWT->CYCCNT;
    
    // Enter Stop mode
    HAL_PWR_EnterSTOPMode(PWR_LOWPOWERREGULATOR_ON, PWR_STOPENTRY_WFI);
    
    // Wakeup: measure latency (cycles to first instruction after WFI)
    uint32_t after = DWT->CYCCNT;
    g_power.wakeup_latency_us = (after - before) / (SystemCoreClock / 1000000);
    g_power.stop_count++;
}

// Optimize: disable unused peripherals before sleep
void pre_sleep_optimize(void) {
    // Disable ADC (high power consumer)
    ADC1->CR2 &= ~ADC_CR2_ADON;
    // Disable USB
    // Disable unused GPIO clocks (keep only wakeup pin)
    RCC->AHB1ENR &= ~(RCC_AHB1ENR_GPIOBEN | RCC_AHB1ENR_GPIOCEN);
    // Reduce system clock before sleep (less wake latency)
    // ... configure to HSI 16MHz ...
    // Flush any pending writes
    __DSB();
    __ISB();
}

// Post-wakeup restore
void post_wakeup_restore(void) {
    // Reconfigure PLL and system clock
    SystemClock_Config();
    // Re-enable peripherals
    RCC->AHB1ENR |= RCC_AHB1ENR_GPIOBEN | RCC_AHB1ENR_GPIOCEN;
    // Re-enable ADC if needed
    // ADC1->CR2 |= ADC_CR2_ADON;
}

// FreeRTOS tickless idle integration
void vApplicationSleep(TickType_t expectedIdleTime) {
    if (expectedIdleTime > 2) {
        // Configure next wakeup via LPTIM or RTC
        schedule_next_wakeup(expectedIdleTime);
        // Enter Stop mode
        pre_sleep_optimize();
        HAL_PWR_EnterSTOPMode(PWR_LOWPOWERREGULATOR_ON, PWR_STOPENTRY_WFI);
        post_wakeup_restore();
        // Adjust RTOS tick count for slept duration
        correct_rtc_tick_count();
    }
}

// In FreeRTOSConfig.h:
// #define configUSE_TICKLESS_IDLE  1
// #define portSUPPRESS_TICKS_AND_SLEEP  vApplicationSleep
```

功耗优化检查清单：

| 优化项 | 节省功耗 | 复杂度 |
|--------|---------|--------|
| Sleep 模式（空闲时） | ~30% | 低 |
| Stop 模式（无任务时） | ~90% | 中 |
| Standby 模式（长时间空闲） | ~99% | 高（重启） |
| 降低时钟频率 | ~50% | 低 |
| 关闭未用外设时钟 | ~10-20% | 低 |
| GPIO 设为模拟输入 | ~5% | 低 |
| 关闭 Flash 后台读写 | ~5% | 低 |
| 使用 LPTIM 替代 TIM | ~15% | 中 |

---

## 17. 安全关键系统中断设计

安全关键系统（汽车电子、医疗器械、航空航天、工业控制）对中断设计有严格要求。一个未处理的中断可能导致电机失控、刹车失效、辐射过量等灾难性后果。本章从 HardFault 恢复、看门狗设计、安全状态机、故障注入测试、ISO 26262 标准五个维度讲解安全中断设计。

### 17.1 硬件故障（HardFault）处理与恢复策略

在安全关键系统中，HardFault 不能简单"死循环等待调试"，必须有明确的恢复策略。常见策略：

| 策略 | 适用场景 | 风险 |
|------|---------|------|
| 系统复位 | 通用安全系统 | 丢失当前状态，可能短暂失控 |
| 降级运行 | 容错系统 | 性能下降但保持基本功能 |
| 安全停止 | 电机/运动控制 | 立即停止输出，进入安全态 |
| 日志+复位 | 可远程升级设备 | 便于事后分析 |

完整的 HardFault 恢复框架：

```c
// Safety-critical HardFault handler with recovery
typedef enum {
    FAULT_RECOVERY_NONE = 0,
    FAULT_RECOVERY_RESET,
    FAULT_RECOVERY_SAFE_STATE,
    FAULT_RECOVERY_DEGRADED
} fault_recovery_t;

typedef struct {
    uint32_t pc, lr, sp, psr;
    uint32_t cfsr, hfsr, bfar, mmfar;
    uint32_t exc_return;
    fault_recovery_t recovery;
    uint32_t fault_count;
    uint32_t last_fault_tick;
} fault_record_t;

static fault_record_t g_fault_record __attribute__((section(".noinit")));

// Naked handler to capture stack frame
__attribute__((naked)) void HardFault_Handler(void) {
    __asm volatile(
        "tst lr, #4                \n"
        "ite eq                    \n"
        "mrseq r0, msp             \n"
        "mrsne r0, psp             \n"
        "b HardFault_Handler_Safe  \n"
    );
}

void HardFault_Handler_Safe(uint32_t *stack_frame) {
    // Capture fault info (use direct memory writes, no function calls)
    g_fault_record.pc  = stack_frame[6];
    g_fault_record.lr  = stack_frame[5];
    g_fault_record.sp  = (uint32_t)stack_frame;
    g_fault_record.psr = stack_frame[7];
    g_fault_record.cfsr = SCB->CFSR;
    g_fault_record.hfsr = SCB->HFSR;
    g_fault_record.bfar = SCB->BFAR;
    g_fault_record.mmfar = SCB->MMFAR;
    g_fault_record.exc_return = __get_LR();
    g_fault_record.fault_count++;
    g_fault_record.last_fault_tick = get_safe_tick();
    
    // Decide recovery strategy based on fault type and frequency
    if (g_fault_record.fault_count > 3) {
        // Too many faults: hard reset
        g_fault_record.recovery = FAULT_RECOVERY_RESET;
    } else if (g_fault_record.cfsr & (1 << 9)) {
        // DIVBYZERO: software bug, reset
        g_fault_record.recovery = FAULT_RECOVERY_RESET;
    } else if (g_fault_record.cfsr & 0xFF00) {
        // BusFault: possibly transient (EMI), try safe state
        g_fault_record.recovery = FAULT_RECOVERY_SAFE_STATE;
    } else {
        g_fault_record.recovery = FAULT_RECOVERY_RESET;
    }
    
    // Execute recovery
    switch (g_fault_record.recovery) {
        case FAULT_RECOVERY_SAFE_STATE:
            enter_safe_state();  // Stop motors, disable outputs
            // Optionally try to resume from faulting instruction
            // (risky, only for transient faults)
            NVIC_SystemReset();  // Then reset to clean state
            break;
        case FAULT_RECOVERY_RESET:
        default:
            // Wait for any pending writes to complete
            __DSB();
            NVIC_SystemReset();
            break;
    }
    while (1) {}  // Never reached
}

// Safe state: disable all dangerous outputs immediately
void enter_safe_state(void) {
    // Disable motor PWM (set to safe duty = 0)
    TIM1->CCER = 0;  // Disable all PWM outputs
    // Disable DMA that might drive dangerous peripherals
    DMA1_Stream0->CR &= ~DMA_SxCR_EN;
    // Set GPIO outputs to safe state (e.g., brake enable)
    GPIOA->BSRR = GPIO_PIN_5;  // Assert brake
    // Disable interrupts except watchdog
    __set_BASEPRI(1 << (8 - __NVIC_PRIO_BITS));  // Keep only prio 0
}
```

### 17.2 看门狗（IWDG/WWDG）配置与中断设计

看门狗是安全系统的"最后防线"。STM32 提供两个看门狗：

| 特性 | IWDG（独立看门狗） | WWDG（窗口看门狗） |
|------|-------------------|-------------------|
| 时钟源 | LSI（独立） | APB1（可监控时钟） |
| 超时范围 | 100µs - 28s | ~58ms（最大） |
| 提前唤醒中断 | 无（部分型号有） | 有（EWI） |
| 适用 | 系统级监控 | 精确时序监控 |

#### 17.2.1 IWDG 配置

```c
// Independent Watchdog (IWDG) - 2 second timeout
void iwdg_init(void) {
    // Enable write access to IWDG_PR and IWDG_RLR
    IWDG->KR = 0x5555;
    // Set prescaler: /256 (LSI=32kHz -> 125Hz)
    IWDG->PR = IWDG_PR_PR_2 | IWDG_PR_PR_1 | IWDG_PR_PR_0;
    // Set reload: 125Hz * 2s = 250
    IWDG->RLR = 250;
    // Reload counter
    IWDG->KR = 0xAAAA;
    // Start IWDG
    IWDG->KR = 0xCCCC;
}

// Feed the dog (call from main loop, NOT from ISR)
void iwdg_feed(void) {
    IWDG->KR = 0xAAAA;
}
```

#### 17.2.2 WWDG 配置（带提前唤醒中断）

```c
// Window Watchdog (WWDG) with Early Wakeup Interrupt
void wwdg_init(void) {
    // Enable WWDG clock
    RCC->APB1ENR |= RCC_APB1ENR_WWDGEN;
    
    // Set prescaler: /4096 * 8 = /8 (PCLK1=42MHz -> ~512Hz)
    WWDG->CFR = WWDG_CFR_WDGTB_1;  // Prescaler /8
    // Set window value: must feed between T[63] and T[40]
    WWDG->CFR |= 0x50;  // Window = 0x50 (80)
    // Enable Early Wakeup Interrupt (fires at T[64] = 0x40)
    WWDG->CFR |= WWDG_CFR_EWI;
    // Set counter initial value
    WWDG->CR = 0x7F;  // Counter = 0x7F (127)
    // Enable WWDG
    WWDG->CR |= WWDG_CR_WDGA;
    
    // Enable NVIC interrupt
    HAL_NVIC_SetPriority(WWDG_IRQn, 0, 0);  // Highest priority
    HAL_NVIC_EnableIRQ(WWDG_IRQn);
}

// WWDG Early Wakeup Interrupt: last chance to log/save before reset
void WWDG_IRQHandler(void) {
    // Clear EWI flag
    WWDG->SR = 0;
    
    // CRITICAL: We're about to be reset, do emergency work
    // - Save critical state to backup register
    // - Set safe GPIO state
    // - Log fault reason
    RTC->BKP0R = 0xDEAD;  // Mark WWDG reset
    
    // Optionally feed to prevent reset (if recovery possible)
    // WWDG->CR = 0x7F;
    
    // If not fed, reset occurs when counter reaches 0x3F
}

// Feed WWDG (must be in window: 0x40 < counter < window_value)
void wwdg_feed(void) {
    WWDG->CR = 0x7F;  // Refresh counter
}
```

看门狗中断设计原则：
1. IWDG 用于检测软件死锁，主循环定期喂狗。
2. WWDG 用于检测时序偏差，EWI 中断是"最后警告"。
3. 喂狗点应选在"关键任务完成后"，而非固定时间。
4. 不要在 ISR 中喂狗（ISR 可能持续运行而主循环已死）。

```c
// Correct watchdog feeding pattern
void main_loop(void) {
    for (;;) {
        // Critical tasks
        bool tasks_ok = run_control_loop();
        bool comms_ok = process_communications();
        
        // Only feed if all critical tasks completed
        if (tasks_ok && comms_ok) {
            iwdg_feed();
            wwdg_feed();
        } else {
            // Don't feed: let watchdog reset system
            log_error("Critical task failed, allowing WDT reset");
        }
    }
}
```

### 17.3 安全状态机设计

安全关键系统应实现明确的状态机，确保任何故障都进入已知安全态：

```c
// Safety state machine for motor control system
typedef enum {
    STATE_INIT = 0,
    STATE_NORMAL,
    STATE_DEGRADED,    // Reduced functionality, still operating
    STATE_SAFE,        // Outputs disabled, waiting for reset
    STATE_FAULT        // Critical fault, immediate stop
} safety_state_t;

static volatile safety_state_t g_safety_state = STATE_INIT;
static volatile uint32_t g_fault_flags = 0;

#define FAULT_OVERCURRENT   (1 << 0)
#define FAULT_OVERTEMP      (1 << 1)
#define FAULT_COMM_LOST     (1 << 2)
#define FAULT_WATCHDOG      (1 << 3)
#define FAULT_BROWNOUT      (1 << 4)

// Transition to safety state (called from ISR or main)
void safety_transition(safety_state_t new_state) {
    __disable_irq();
    safety_state_t old = g_safety_state;
    g_safety_state = new_state;
    __enable_irq();
    
    // Execute state entry actions
    switch (new_state) {
        case STATE_DEGRADED:
            // Reduce motor speed to 30%
            TIM1->CCR1 = (TIM1->ARR * 30) / 100;
            break;
        case STATE_SAFE:
            // Disable motor, enable brake
            TIM1->CCER = 0;
            GPIOA->BSRR = GPIO_PIN_5;  // Brake on
            break;
        case STATE_FAULT:
            // Immediate full stop
            TIM1->CCER = 0;
            GPIOA->BSRR = GPIO_PIN_5;  // Brake
            GPIOB->BSRR = GPIO_PIN_8;  // Fault LED
            // Trigger watchdog reset in 100ms
            schedule_fault_reset();
            break;
        default:
            break;
    }
}

// Overcurrent ISR (highest priority, no RTOS API)
void COMP_IRQHandler(void) {
    // Comparator detected overcurrent
    g_fault_flags |= FAULT_OVERCURRENT;
    // Immediate hardware action: disable PWM
    TIM1->CCER = 0;
    // Transition to fault state
    g_safety_state = STATE_FAULT;
    // Log event
    fault_log(FAULT_OVERCURRENT, HAL_GetTick());
}

// Monitor task: checks for faults, manages state transitions
void safety_monitor_task(void *pv) {
    for (;;) {
        // Check temperature
        if (read_temperature() > TEMP_THRESHOLD) {
            g_fault_flags |= FAULT_OVERTEMP;
            safety_transition(STATE_SAFE);
        }
        // Check communication timeout
        if (HAL_GetTick() - last_comm_tick > 1000) {
            g_fault_flags |= FAULT_COMM_LOST;
            safety_transition(STATE_DEGRADED);
        }
        // Check if all faults cleared (for recovery)
        if (g_fault_flags == 0 && g_safety_state == STATE_DEGRADED) {
            safety_transition(STATE_NORMAL);
        }
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}
```

### 17.4 故障注入测试

故障注入测试是验证安全机制有效性的关键方法。通过人为注入故障，验证系统是否正确响应：

```c
// Fault injection framework for testing safety mechanisms
typedef enum {
    INJECT_NONE = 0,
    INJECT_HARDFFAULT_NULLPTR,    // Null pointer dereference
    INJECT_HARDFFAULT_DIVZERO,    // Division by zero
    INJECT_HARDFFAULT_STACK,      // Stack overflow
    INJECT_OVERCURRENT,           // Simulate overcurrent
    INJECT_WATCHDOG_TIMEOUT,      // Stop feeding watchdog
    INJECT_COMM_TIMEOUT,          // Simulate comm loss
    INJECT_FLASH_ERROR            // Simulate flash corruption
} fault_inject_type_t;

// Inject a fault for testing
void fault_inject(fault_inject_type_t type) {
    switch (type) {
        case INJECT_HARDFFAULT_NULLPTR: {
            // Dereference null pointer (triggers HardFault)
            volatile uint32_t *p = NULL;
            *p = 0xDEAD;
            break;
        }
        case INJECT_HARDFFAULT_DIVZERO: {
            // Enable divide-by-zero trap
            SCB->CCR |= SCB_CCR_DIV_0_TRP_Msk;
            volatile uint32_t zero = 0;
            volatile uint32_t result = 100 / zero;
            (void)result;
            break;
        }
        case INJECT_HARDFFAULT_STACK: {
            // Recursive function to overflow stack
            void overflow(uint32_t depth) {
                volatile uint8_t buf[256];
                buf[0] = depth;
                if (depth > 0) overflow(depth - 1);
            }
            overflow(100);
            break;
        }
        case INJECT_OVERCURRENT:
            // Simulate comparator trigger
            g_fault_flags |= FAULT_OVERCURRENT;
            safety_transition(STATE_FAULT);
            break;
        case INJECT_WATCHDOG_TIMEOUT:
            // Stop feeding watchdog
            g_stop_wdt_feed = true;
            break;
        case INJECT_COMM_TIMEOUT:
            // Simulate last_comm_tick being old
            last_comm_tick = HAL_GetTick() - 5000;
            break;
        case INJECT_FLASH_ERROR:
            // Corrupt a flash region (if writable)
            break;
        default:
            break;
    }
}

// Test runner: inject faults and verify recovery
void run_safety_tests(void) {
    struct {
        fault_inject_type_t type;
        const char *name;
        uint32_t expected_state;
    } tests[] = {
        {INJECT_OVERCURRENT, "Overcurrent", STATE_FAULT},
        {INJECT_COMM_TIMEOUT, "Comm timeout", STATE_DEGRADED},
        {INJECT_WATCHDOG_TIMEOUT, "WDT timeout", STATE_FAULT},
        // ... more tests
    };
    
    for (int i = 0; i < sizeof(tests)/sizeof(tests[0]); i++) {
        printf("Test %d: %s... ", i, tests[i].name);
        g_safety_state = STATE_NORMAL;
        g_fault_flags = 0;
        fault_inject(tests[i].type);
        HAL_Delay(500);  // Wait for state machine to settle
        if (g_safety_state == tests[i].expected_state) {
            printf("PASS\n");
        } else {
            printf("FAIL (got state %d, expected %d)\n",
                   g_safety_state, tests[i].expected_state);
        }
    }
}
```

### 17.5 ISO 26262 与中断设计要求

ISO 26262 是汽车功能安全国际标准，将安全等级分为 ASIL A-D（D 最高）。中断设计需满足以下要求：

| ASIL 等级 | 中断设计要求 | 看门狗 | 故障检测 |
|-----------|-------------|--------|---------|
| QM | 基本可靠 | 可选 | 软件 |
| A | 错误检测 | 推荐 | 软件 + 简单硬件 |
| B | 错误检测 + 容错 | 必需 | 硬件冗余 |
| C | 故障容错 + 降级 | 双看门狗 | 硬件 + 时间冗余 |
| D | 高完整性 + 故障安全 | 双看门狗 + 监控 | 多样性冗余 |

ASIL-D 系统的中断设计要点：

1. **中断源冗余**：关键中断（如过流检测）使用两个独立硬件（如两个比较器），两个都触发才算故障。
2. **时间监控**：每个中断的执行时间被监控，超时视为故障。
3. **多样性冗余**：关键算法用两种不同实现（如浮点和定点），结果不一致则故障。
4. **看门狗独立**：IWDG + WWDG 双看门狗，时钟源独立。
5. **故障安全态**：任何故障都进入预定义的安全态（如电机断电、阀门关闭）。

```c
// ASIL-D compliant interrupt design (simplified)
// Dual-comparator overcurrent detection
void COMP1_IRQHandler(void) {  // Primary detection
    g_overcurrent_primary = 1;
    check_overcurrent_consensus();
}

void COMP2_IRQHandler(void) {  // Secondary (independent hardware)
    g_overcurrent_secondary = 1;
    check_overcurrent_consensus();
}

// Consensus check: both must agree
void check_overcurrent_consensus(void) {
    if (g_overcurrent_primary && g_overcurrent_secondary) {
        // Both detected: definitely overcurrent
        safety_transition(STATE_FAULT);
    } else if (g_overcurrent_primary || g_overcurrent_secondary) {
        // Only one: possible sensor failure
        g_fault_flags |= FAULT_SENSOR_MISMATCH;
        safety_transition(STATE_SAFE);  // Cautious stop
    }
    // Reset flags after handling
    g_overcurrent_primary = 0;
    g_overcurrent_secondary = 0;
}

// Time monitoring: ISR must complete within deadline
void TIM1_UP_IRQHandler(void) {
    uint32_t start = DWT->CYCCNT;
    TIM1->SR = ~TIM_SR_UIF;
    
    // ... ISR body ...
    
    uint32_t elapsed = DWT->CYCCNT - start;
    if (elapsed > ISR_DEADLINE_CYCLES) {
        // ISR took too long: timing violation
        g_fault_flags |= FAULT_ISR_TIMEOUT;
        safety_transition(STATE_SAFE);
    }
}
```

---

## 18. 中断性能基准测试

中断性能是实时系统的核心指标。本章系统讲解中断延迟测量方法、各 Cortex-M 核心的延迟对比、ISR 执行时间测量、抖动分析与消除、Cache 对延迟的影响，为系统设计提供量化依据。

### 18.1 中断延迟测量方法

中断延迟测量有三种主流方法，精度与复杂度递增：

#### 18.1.1 GPIO 翻转法（示波器/逻辑分析仪）

最直观的方法：用外部信号触发中断，在 ISR 第一条指令翻转 GPIO，用示波器测量触发信号到 GPIO 翻转的时间。

```c
// GPIO toggle method for interrupt latency measurement
// Hardware: function generator -> EXTI0 pin, oscilloscope on EXTI0 + PA0
void latency_measure_gpio_init(void) {
    // PA0: output (probe pin)
    RCC->AHB1ENR |= RCC_AHB1ENR_GPIOAEN;
    GPIOA->MODER &= ~GPIO_MODER_MODER0;
    GPIOA->MODER |= GPIO_MODER_MODER0_0;  // Output
    GPIOA->OSPEEDR |= GPIO_OSPEEDER_OSPEEDR0;  // High speed
    
    // PA1: input with EXTI (trigger pin)
    RCC->AHB1ENR |= RCC_AHB1ENR_GPIOAEN;
    RCC->APB2ENR |= RCC_APB2ENR_SYSCFGEN;
    GPIOA->MODER &= ~GPIO_MODER_MODER1;  // Input
    SYSCFG->EXTICR[0] = SYSCFG_EXTICR1_EXTI1_PA;
    EXTI->IMR |= EXTI_IMR_MR1;
    EXTI->RTSR |= EXTI_RTSR_TR1;  // Rising edge
    HAL_NVIC_SetPriority(EXTI1_IRQn, 0, 0);  // Highest priority
    HAL_NVIC_EnableIRQ(EXTI1_IRQn);
}

// ISR: first instruction MUST be GPIO toggle
void EXTI1_IRQHandler(void) {
    GPIOA->BSRR = GPIO_PIN_0;  // Set PA0 high (FIRST instruction)
    // ... any ISR work ...
    EXTI->PR = EXTI_PR_PR1;     // Clear pending
    GPIOA->BSRR = GPIO_PIN_0 << 16;  // Set PA0 low
}
// On oscilloscope: measure time from PA1 rising edge to PA0 rising edge
// This is the hardware interrupt latency
```

#### 18.1.2 定时器输入捕获法

使用定时器的输入捕获功能自动测量延迟，无需示波器：

```c
// Timer input capture method for automatic latency measurement
// TIM2_CH1 captures trigger, TIM2_CH2 captures ISR response
void latency_measure_timercapture_init(void) {
    // Configure TIM2 with two capture channels
    TIM2->PSC = 0;           // No prescaler (count at full clock)
    TIM2->ARR = 0xFFFFFFFF;  // Max period
    TIM2->CCMR1 = TIM_CCMR1_CC1S_0 |   // CH1 input, TI1
                  TIM_CCMR1_CC2S_1;    // CH2 input, TI2
    TIM2->CCER = TIM_CCER_CC1E |       // Enable CH1 capture
                 TIM_CCER_CC2E |       // Enable CH2 capture
                 TIM_CCER_CC2P;        // CH2 falling edge
    TIM2->DIER = TIM_DIER_CC1IE;       // Interrupt on CH1 capture
    TIM2->CR1 = TIM_CR1_CEN;           // Start timer
    
    HAL_NVIC_SetPriority(TIM2_IRQn, 0, 0);
    HAL_NVIC_EnableIRQ(TIM2_IRQn);
}

static volatile uint32_t cap1, cap2, latency;

void TIM2_IRQHandler(void) {
    if (TIM2->SR & TIM_SR_CC1IF) {
        cap1 = TIM2->CCR1;  // Capture trigger time
        // Immediately toggle response pin (connected to CH2)
        GPIOA->BSRR = GPIO_PIN_0;
        GPIOA->BSRR = GPIO_PIN_0 << 16;  // Quick pulse
    }
    if (TIM2->SR & TIM_SR_CC2IF) {
        cap2 = TIM2->CCR2;  // Capture response time
        latency = cap2 - cap1;
    }
    TIM2->SR = 0;  // Clear all flags
}
```

#### 18.1.3 DWT 周期计数器法

使用 DWT CYCCNT 软件测量，无需额外硬件，但需要预先记录触发时刻：

```c
// DWT-based latency measurement (software only)
static volatile uint32_t trigger_cycle;
static volatile uint32_t latency_cycles;
static volatile uint32_t latency_min = 0xFFFFFFFF;
static volatile uint32_t latency_max = 0;
static volatile uint32_t latency_sum = 0;
static volatile uint32_t latency_count = 0;

// Arm the measurement: record current cycle count
void latency_arm(void) {
    __DSB();
    __ISB();
    trigger_cycle = DWT->CYCCNT;
}

// ISR: read cycle count first
void EXTI0_IRQHandler(void) {
    uint32_t now = DWT->CYCCNT;  // FIRST: read cycle counter
    latency_cycles = now - trigger_cycle;
    
    // Update statistics
    if (latency_cycles < latency_min) latency_min = latency_cycles;
    if (latency_cycles > latency_max) latency_max = latency_cycles;
    latency_sum += latency_cycles;
    latency_count++;
    
    EXTI->PR = EXTI_PR_PR0;
}

// Report statistics
void latency_report(void) {
    if (latency_count == 0) return;
    uint32_t avg = latency_sum / latency_count;
    printf("Interrupt Latency Statistics:\n");
    printf("  Samples: %lu\n", latency_count);
    printf("  Min: %lu cycles (%lu ns)\n", latency_min,
           (latency_min * 1000000000ULL) / SystemCoreClock);
    printf("  Max: %lu cycles (%lu ns)\n", latency_max,
           (latency_max * 1000000000ULL) / SystemCoreClock);
    printf("  Avg: %lu cycles (%lu ns)\n", avg,
           (avg * 1000000000ULL) / SystemCoreClock);
    printf("  Jitter: %lu cycles (%lu ns)\n", latency_max - latency_min,
           ((latency_max - latency_min) * 1000000000ULL) / SystemCoreClock);
}
```

### 18.2 不同 Cortex-M 核心延迟对比表

各 Cortex-M 核心的中断延迟对比（理论值，零等待内存）：

| 核心 | 架构 | 最小延迟 | 含 FPU 保存 | 尾链 | 迟来 | 典型应用 |
|------|------|---------|------------|------|------|---------|
| Cortex-M0 | ARMv6-M | 15 cycles | N/A | 有限 | 不支持 | 低成本 MCU |
| Cortex-M0+ | ARMv6-M | 15 cycles | N/A | 有限 | 不支持 | 超低功耗 |
| Cortex-M3 | ARMv7-M | 12 cycles | N/A | 支持 | 支持 | 主流 32 位 |
| Cortex-M4 | ARMv7E-M | 12 cycles | 28 cycles | 支持 | 支持 | DSP/控制 |
| Cortex-M7 | ARMv7E-M | 12 cycles | 28 cycles | 支持 | 支持 | 高性能 |
| Cortex-M23 | ARMv8-M | 16 cycles | N/A | 支持 | 支持 | TrustZone 基线 |
| Cortex-M33 | ARMv8-M | 12 cycles | 28 cycles | 支持 | 支持 | 安全 + 性能 |
| Cortex-M55 | ARMv8.1-M | 12 cycles | 28 cycles | 支持 | 支持 | AI/ML |

实际测量值因内存等待、缓存、总线负载而高于理论值。下表为 STM32 各型号在默认配置下的实测延迟：

| MCU 型号 | 核心 | 主频 | Flash WS | 实测延迟 | ISR 在 SRAM | 备注 |
|---------|------|------|---------|---------|------------|------|
| STM32F030 | M0 | 48MHz | 1 | ~22 cyc | ~17 cyc | 无 ART |
| STM32F103 | M3 | 72MHz | 2 | ~18 cyc | ~13 cyc | 有 ART |
| STM32F407 | M4 | 168MHz | 5 | ~20 cyc | ~12 cyc | ART 加速 |
| STM32F746 | M7 | 216MHz | L1 | ~14 cyc | ~12 cyc | L1 缓存 |
| STM32H743 | M7 | 480MHz | L1 | ~13 cyc | ~12 cyc | 双发射 |
| STM32G474 | M4 | 170MHz | 4 | ~19 cyc | ~12 cyc | |
| STM32L432 | M4 | 80MHz | 2 | ~18 cyc | ~12 cyc | 低功耗 |

注意：数据缓存（D-Cache）对延迟的影响取决于缓存命中率。命中率低时，D-Cache 反而增加延迟（缓存未命中惩罚）。

### 18.3 ISR 执行时间测量代码

测量每个 ISR 的执行时间，定位性能瓶颈：

```c
// ISR execution time profiler
#define MAX_ISR_TRACKED  32

typedef struct {
    uint32_t count;
    uint32_t total_cycles;
    uint32_t min_cycles;
    uint32_t max_cycles;
    uint32_t last_cycles;
    const char *name;
} isr_profile_t;

static isr_profile_t g_isr_profiles[MAX_ISR_TRACKED];

void isr_profile_init(void) {
    for (int i = 0; i < MAX_ISR_TRACKED; i++) {
        g_isr_profiles[i].min_cycles = 0xFFFFFFFF;
    }
}

// Macros to instrument ISR
#define ISR_PROFILE_ENTER(id)  uint32_t _isr_start = DWT->CYCCNT
#define ISR_PROFILE_EXIT(id)   isr_profile_record(id, DWT->CYCCNT - _isr_start)

void isr_profile_record(uint32_t id, uint32_t cycles) {
    isr_profile_t *p = &g_isr_profiles[id];
    p->count++;
    p->total_cycles += cycles;
    p->last_cycles = cycles;
    if (cycles < p->min_cycles) p->min_cycles = cycles;
    if (cycles > p->max_cycles) p->max_cycles = cycles;
}

// Instrumented ISR example
void USART1_IRQHandler(void) {
    ISR_PROFILE_ENTER(0);  // ID 0 for USART1
    uint32_t sr = USART1->SR;
    if (sr & USART_SR_RXNE) {
        uint8_t b = USART1->DR;
        ring_put(&rx_ring, b);
    }
    ISR_PROFILE_EXIT(0);
}

// Report ISR profiling results
void isr_profile_report(void) {
    printf("=== ISR Execution Time Profile ===\n");
    printf("%-12s %8s %8s %8s %8s %10s\n",
           "ISR", "Count", "Min", "Max", "Avg", "Total(ms)");
    for (int i = 0; i < MAX_ISR_TRACKED; i++) {
        isr_profile_t *p = &g_isr_profiles[i];
        if (p->count == 0) continue;
        uint32_t avg = p->total_cycles / p->count;
        float total_ms = (float)p->total_cycles * 1000.0f / SystemCoreClock;
        printf("%-12s %8lu %8lu %8lu %8lu %10.2f\n",
               p->name ? p->name : "ISR",
               p->count, p->min_cycles, p->max_cycles, avg, total_ms);
    }
}
```

### 18.4 中断抖动（Jitter）分析与减少方法

抖动是同一中断多次响应延迟的变化范围。硬实时系统要求抖动有界。

抖动来源：

| 来源 | 影响（cycles） | 可消除性 |
|------|--------------|---------|
| Flash 等待变化 | 3-5 | 移到 SRAM |
| 缓存未命中 | 10-50 | 锁定缓存 |
| DMA 总线占用 | 5-20 | 限制 DMA 优先级 |
| 多周期指令 | 2-12 | 避免在关键路径用 |
| 嵌套中断 | 0-100+ | 减少嵌套 |
| RTOS 临界区 | 10-500 | 缩短临界区 |
| 调度延迟 | 0-1000+ | 提高中断优先级 |

抖动测量与分析代码：

```c
// Jitter analysis: measure latency distribution
#define JITTER_HISTOGRAM_BINS  32
#define JITTER_BIN_SIZE        2  // cycles per bin

static volatile uint32_t g_jitter_hist[JITTER_HISTOGRAM_BINS];
static volatile uint32_t g_jitter_min = 0xFFFFFFFF;
static volatile uint32_t g_jitter_max = 0;
static volatile uint32_t g_jitter_count = 0;

void jitter_record(uint32_t latency) {
    g_jitter_count++;
    if (latency < g_jitter_min) g_jitter_min = latency;
    if (latency > g_jitter_max) g_jitter_max = latency;
    // Update histogram
    uint32_t bin = latency / JITTER_BIN_SIZE;
    if (bin >= JITTER_HISTOGRAM_BINS) bin = JITTER_HISTOGRAM_BINS - 1;
    g_jitter_hist[bin]++;
}

void jitter_report(void) {
    printf("=== Interrupt Jitter Analysis ===\n");
    printf("Samples: %lu\n", g_jitter_count);
    printf("Min latency: %lu cycles\n", g_jitter_min);
    printf("Max latency: %lu cycles\n", g_jitter_max);
    printf("Jitter (max-min): %lu cycles\n", g_jitter_max - g_jitter_min);
    printf("\nHistogram (cycles: count):\n");
    for (int i = 0; i < JITTER_HISTOGRAM_BINS; i++) {
        if (g_jitter_hist[i] > 0) {
            printf("  %3d-%3d: %lu ",
                   i * JITTER_BIN_SIZE, (i + 1) * JITTER_BIN_SIZE - 1,
                   g_jitter_hist[i]);
            // Print bar chart
            for (uint32_t j = 0; j < g_jitter_hist[i] && j < 50; j++) {
                printf("#");
            }
            printf("\n");
        }
    }
}

// Histogram output example:
//   12- 13: 842 ########
//   14- 15: 156 #
//   16- 17:  12
//   18- 19:   3
//   20- 21:   1
// Most samples at 12-13 cycles (ideal), few outliers up to 20+ (jitter)
```

减少抖动的策略：

```c
// Reduce jitter: place critical ISR in SRAM, disable cache pollution
__attribute__((section(".ramfunc"), noinline))
void TIM1_UP_TIM10_IRQHandler(void) {
    // This ISR runs from SRAM: no Flash wait state jitter
    TIM1->SR = ~TIM_SR_UIF;
    // Critical control loop code here
    // No division, no memory allocation, minimal branches
    float err = g_target - g_actual;
    g_integral += err * g_ki;
    float out = err * g_kp + g_integral;
    TIM1->CCR1 = (uint32_t)(out * TIM1->ARR);
}

// Lock critical ISR code into L1 I-Cache (Cortex-M7)
void lock_isr_in_icache(void) {
    // M7 supports cache lockdown
    // Configure ITCM (Instruction Tightly Coupled Memory) for critical ISR
    // Or use cache lockdown registers
    SCB->CCR |= SCB_CCR_DCACHE_Msk | SCB_CCR_ICACHE_Msk;  // Enable caches
    // For M7: place ISR in ITCM (0x00000000) for zero wait state
}
```

### 18.5 Cache 对中断延迟的影响

Cortex-M7 引入 L1 缓存（I-Cache + D-Cache），对中断延迟有显著影响：

| 配置 | 延迟影响 | 适用场景 |
|------|---------|---------|
| I-Cache 关闭 | +5-20 cycles（Flash WS） | 调试 |
| I-Cache 开启 | 命中时 -5 cycles | 通用 |
| I-Cache 锁定 | 0 cycles 抖动 | 硬实时 |
| D-Cache 关闭 | +2-10 cycles | 简单外设 |
| D-Cache 开启 | 命中时快，未命中慢 | 通用 |
| ITCM 放 ISR | 0 cycles | 关键 ISR |
| DTCM 放数据 | 0 cycles | 关键数据 |

```c
// Cache configuration for optimal interrupt latency
void cache_optimize_for_interrupts(void) {
    // Enable I-Cache and D-Cache
    SCB->CCR |= SCB_CCR_ICACHE_Msk;
    __DSB();
    __ISB();
    SCB->CCR |= SCB_CCR_DCACHE_Msk;
    __DSB();
    __ISB();
    
    // For Cortex-M7: use ITCM for critical ISR
    // ITCM is at 0x00000000, zero wait state, not affected by cache
    // Place ISR in .itcm section
}

// Place critical ISR in ITCM (Cortex-M7 only)
__attribute__((section(".itcm"), noinline))
void motor_control_isr(void) {
    // Runs from ITCM: deterministic, zero wait state
    // No cache miss jitter
}

// Place critical data in DTCM
__attribute__((section(".dtcm")))
volatile uint32_t g_control_target;  // Accessed from ISR, zero latency

// Cache maintenance before reading DMA buffer
void invalidate_dcache_for_dma_buffer(uint32_t *buf, uint32_t size) {
    // DMA writes to memory bypassing D-Cache
    // Must invalidate cache before CPU reads
    uint32_t addr = (uint32_t)buf;
    uint32_t end = addr + size;
    // Align to cache line (32 bytes on M7)
    addr &= ~31;
    while (addr < end) {
        SCB->DCIMVAC = addr;  // Invalidate by MVA to PoC
        addr += 32;
    }
    __DSB();
    __ISB();
}

// Clean D-Cache before DMA reads from memory
void clean_dcache_for_dma_buffer(uint32_t *buf, uint32_t size) {
    // CPU writes to memory via D-Cache
    // Must clean cache before DMA reads
    uint32_t addr = (uint32_t)buf;
    uint32_t end = addr + size;
    addr &= ~31;
    while (addr < end) {
        SCB->DCCMVAC = addr;  // Clean by MVA to PoC
        addr += 32;
    }
    __DSB();
    __ISB();
}
```

Cache 与 DMA 协作是 M7 中断设计的常见陷阱：DMA 直接写内存，若 D-Cache 未失效，CPU 读到的是缓存旧数据。必须在 DMA 完成中断中失效相关缓存。

### 18.6 性能基准测试完整框架

```c
// Complete interrupt benchmark framework
typedef struct {
    // Latency statistics
    uint32_t lat_min, lat_max, lat_sum, lat_count;
    // Execution time statistics
    uint32_t exec_min, exec_max, exec_sum;
    // Jitter
    uint32_t jitter_max;
    // CPU time percentage
    uint32_t cpu_time_percent;
} bench_result_t;

static bench_result_t g_bench;

void benchmark_run(uint32_t duration_ms) {
    // Reset stats
    memset(&g_bench, 0, sizeof(g_bench));
    g_bench.lat_min = 0xFFFFFFFF;
    g_bench.exec_min = 0xFFFFFFFF;
    
    // Enable measurement
    g_bench_enabled = true;
    
    // Run for specified duration
    uint32_t start = HAL_GetTick();
    while (HAL_GetTick() - start < duration_ms) {
        // Trigger interrupts at known intervals
        trigger_test_interrupt();
        HAL_Delay(1);  // 1ms interval
    }
    
    g_bench_enabled = false;
    
    // Compute results
    if (g_bench.lat_count > 0) {
        uint32_t lat_avg = g_bench.lat_sum / g_bench.lat_count;
        uint32_t exec_avg = g_bench.exec_sum / g_bench.lat_count;
        g_bench.jitter_max = g_bench.lat_max - g_bench.lat_min;
        g_bench.cpu_time_percent = 
            (g_bench.exec_sum * 100) / (duration_ms * 1000 * (SystemCoreClock / 1000000));
        
        printf("=== Benchmark Results (%lu ms) ===\n", duration_ms);
        printf("Samples: %lu\n", g_bench.lat_count);
        printf("Latency: min=%lu max=%lu avg=%lu jitter=%lu cycles\n",
               g_bench.lat_min, g_bench.lat_max, lat_avg, g_bench.jitter_max);
        printf("Exec time: min=%lu max=%lu avg=%lu cycles\n",
               g_bench.exec_min, g_bench.exec_max, exec_avg);
        printf("CPU time in ISR: %lu.%lu%%\n",
               g_bench.cpu_time_percent / 10, g_bench.cpu_time_percent % 10);
    }
}
```

---

## 19. 高级中断控制器（NVIC 扩展）

随着 ARMv8-M 架构的引入，Cortex-M23/M33 增加了 TrustZone 安全扩展，中断控制器也随之扩展。本章讲解 TrustZone 对中断系统的影响、中断目标（Secure/Non-secure）配置、SAU 配置、以及多核 Cortex-M 的中断路由。

### 19.1 Cortex-M23/M33 的 TrustZone 安全扩展

TrustZone 将处理器状态分为 **Secure** 和 **Non-secure** 两个世界，每个世界有独立的栈、MPU、NVIC 视图。中断也被标记为 Secure 或 Non-secure，确保 Non-secure 代码无法干扰 Secure 中断。

TrustZone 中断架构要点：
- **Secure NVIC**：管理 Secure 中断，仅 Secure 代码可配置。
- **Non-secure NVIC**：管理 Non-secure 中断，Non-secure 代码可配置。
- **中断目标**：每个中断可标记为 Secure 或 Non-secure。
- **SGI（Secure Gateway Interrupt）**：允许 Non-secure 代码触发 Secure 中断（通过 SG 指令入口）。

TrustZone 中断分类：

| 中断类型 | 配置者 | 可被 Non-secure 触发 | 可被 Non-secure 屏蔽 |
|---------|--------|---------------------|---------------------|
| Secure 中断（非 SGI） | 仅 Secure | 否 | 否 |
| SGI（Secure Gateway） | 仅 Secure | 是（通过 SG 指令） | 否 |
| Non-secure 中断 | Non-secure | 是 | 是 |

### 19.2 中断目标（Secure/Non-secure）配置

在 Cortex-M33 上，中断的目标世界由 NVIC 的 ITNS（Interrupt Target Non-Secure）寄存器控制：

```c
// Configure interrupt targeting on Cortex-M33 with TrustZone
// ITNS register: bit n = 1 means IRQn is Non-secure target

void configure_irq_targeting(void) {
    // Mark USART1 as Non-secure (Non-secure world can handle it)
    NVIC->ITNS[0] |= (1UL << (USART1_IRQn & 0x1F));
    
    // Mark TIM2 as Secure (only Secure world handles it)
    NVIC->ITNS[0] &= ~(1UL << (TIM2_IRQn & 0x1F));
    
    // Mark all DMA interrupts as Non-secure
    NVIC->ITNS[0] |= (1UL << (DMA1_Stream0_IRQn & 0x1F));
    NVIC->ITNS[0] |= (1UL << (DMA1_Stream1_IRQn & 0x1F));
}

// Secure side: enable a Secure interrupt
void secure_enable_irq(IRQn_Type irqn) {
    // Secure NVIC view (accessible from Secure state only)
    NVIC_S->ISER[irqn >> 5] = (1UL << (irqn & 0x1F));
    NVIC_S->IP[irqn] = 5 << (8 - __NVIC_PRIO_BITS);  // Priority 5
}

// Non-secure side: enable a Non-secure interrupt
void nonsecure_enable_irq(IRQn_Type irqn) {
    // Non-secure NVIC view (only sees Non-secure interrupts)
    NVIC_NS->ISER[irqn >> 5] = (1UL << (irqn & 0x1F));
    NVIC_NS->IP[irqn] = 10 << (8 - __NVIC_PRIO_BITS);  // Priority 10
}
```

ITNS 寄存器位域：

| 寄存器 | 地址偏移 | 作用 |
|--------|---------|------|
| ITNS0-7 | 0xE000E380 + 4*n | bit[i]=1 表示 IRQ (32*n+i) 目标为 Non-secure |

### 19.3 SAU（Security Attribution Unit）配置

SAU 类似 MPU，但用于划分内存/外设的 Secure/Non-secure 属性。SAU 配置决定中断访问的外设寄存器是否可被 Non-secure 代码操作：

```c
// SAU configuration for Cortex-M33
// Define memory regions as Secure or Non-secure
typedef struct {
    uint32_t start;
    uint32_t end;
    uint32_t attr;  // 0=Non-secure, 1=Secure, 3=Non-secure Callable
} sau_region_t;

static const sau_region_t sau_regions[] = {
    // Non-secure flash region (application code)
    {0x08040000, 0x0807FFFF, 0},  // Non-secure
    // Secure flash region (bootloader, crypto keys)
    {0x08000000, 0x0803FFFF, 1},  // Secure
    // Non-secure RAM (application data)
    {0x20008000, 0x2001FFFF, 0},  // Non-secure
    // Secure RAM (secure state data)
    {0x20000000, 0x20007FFF, 1},  // Secure
    // Non-secure Callable entry point (SG instruction region)
    {0x08040000, 0x080401FF, 3},  // Non-secure Callable
};

void sau_init(void) {
    SAU->CTRL = 0;  // Disable during config
    
    for (int i = 0; i < sizeof(sau_regions)/sizeof(sau_regions[0]); i++) {
        SAU->RNR = i;  // Select region number
        SAU->RBAR = sau_regions[i].start;
        SAU->RLAR = sau_regions[i].end | (sau_regions[i].attr << 1);
    }
    
    // Enable SAU
    SAU->CTRL = 1;  // Enable SAU
    __DSB();
    __ISB();
}
```

SAU 区域属性：

| 属性值 | 名称 | 含义 |
|--------|------|------|
| 0 | Non-secure | Non-secure 代码可直接访问 |
| 1 | Secure | 仅 Secure 代码可访问 |
| 3 | Non-secure Callable | 包含 SG 指令，Non-secure 可通过此入口调用 Secure 函数 |

### 19.4 Secure Gateway（SG）与跨世界中断

Non-secure 代码无法直接调用 Secure 函数，必须通过 SG（Secure Gateway）指令入口。SG 区域中的函数以 SG 指令开头：

```c
// Secure function callable from Non-secure (must be in NSC region)
// The compiler inserts SG instruction at function entry
__attribute__((cmse_nonsecure_entry))
uint32_t secure_crypto_service(uint32_t data) {
    // This function runs in Secure state
    // Can access Secure resources (keys, crypto engine)
    uint32_t result = aes_encrypt(data, secure_key);
    return result;
}

// Non-secure side calls it like a normal function
// But hardware transitions to Secure state via SG instruction
void nonsecure_task(void) {
    uint32_t encrypted = secure_crypto_service(plaintext);
    // Now back in Non-secure state
}
```

中断场景下的跨世界调用：
- Non-secure 中断的 ISR 运行在 Non-secure 状态。
- Secure 中断的 ISR 运行在 Secure 状态。
- 当 Secure 中断抢占 Non-secure 中断时，硬件自动切换到 Secure 栈。

### 19.5 多核 Cortex-M 的中断路由

Cortex-M 多核系统（如 STM32H7 双核 M7+M4）需要中断路由机制，决定哪个核处理哪个中断：

| 路由方式 | 机制 | 适用场景 |
|---------|------|---------|
| 静态分配 | 每个外设绑定到一个核 | 简单分工 |
| 动态路由 | 通过共享邮箱分配 | 负载均衡 |
| 核间中断 | 一个核触发另一个核的中断 | 事件通知 |

STM32H7 双核中断路由示例：

```c
// STM32H7 dual-core (M7 + M4) interrupt routing
// M7 is primary (CM7), M4 is secondary (CM4)

// Configure EXTI for M4 core (M7 won't see it)
void exti_route_to_m4(void) {
    // Use EXTI_C1IMR1 (M7) vs EXTI_C2IMR1 (M4)
    // Standard EXTI->IMR1 is M7 by default
    EXTI->C2IMR1 |= EXTI_IMR1_MR0;  // Enable line 0 for M4
    EXTI->C2EMR1 |= EXTI_EMR1_MR0;  // Event for M4
    // M7 mask: do NOT set EXTI->IMR1 bit 0
}

// Inter-core interrupt via HSEM (Hardware Semaphore)
void notify_m4_core(void) {
    // Use HSEM to signal M4
    HSEM->RLR[0] = 0x1;  // Lock semaphore 0 with core ID 1 (M7)
    // M4's HSEM interrupt fires, M4 reads semaphore data
}

// M4 side: HSEM interrupt handler
void HSEM2_IRQHandler(void) {
    if (HSEM2->RISR & (1 << 0)) {
        // Semaphore 0 was locked by M7
        uint32_t msg = shared_mailbox->message;
        process_message_from_m7(msg);
        HSEM2->R[0] = 0x1;  // Release semaphore
    }
}
```

多核中断设计要点：
1. **外设归属**：明确每个外设属于哪个核，避免双核同时配置同一外设。
2. **共享资源**：用 HSEM（硬件信号量）保护共享内存/外设。
3. **核间通信**：用专用核间中断（如 SGIs 或厂商邮箱）传递事件。
4. **时钟同步**：双核时钟可能不同步，跨核时间戳需用共享定时器。

```c
// Generic multi-core interrupt routing table (pseudocode)
typedef struct {
    uint32_t irqn;
    uint32_t target_core;  // 0=CM7, 1=CM4
    uint32_t priority;
    void (*handler)(void);
} irq_routing_t;

static const irq_routing_t routing_table[] = {
    {USART1_IRQn,  0, 6, USART1_IRQHandler},    // M7 handles UART
    {USART2_IRQn,  1, 6, USART2_IRQHandler},    // M4 handles UART2
    {TIM1_IRQn,    0, 1, TIM1_IRQHandler},      // M7 motor control
    {SPI1_IRQn,    1, 7, SPI1_IRQHandler},      // M4 SPI display
    {DMA1_Stream0_IRQn, 0, 2, DMA1_Stream0_IRQHandler},  // M7 DMA
};

void apply_routing_table(void) {
    for (int i = 0; i < sizeof(routing_table)/sizeof(routing_table[0]); i++) {
        const irq_routing_t *r = &routing_table[i];
        if (r->target_core == get_current_core_id()) {
            NVIC_SetPriority((IRQn_Type)r->irqn, r->priority);
            NVIC_EnableIRQ((IRQn_Type)r->irqn);
        }
    }
}
```

---

## 20. 实战案例集

本章通过 5 个完整实战案例，展示中断设计在不同应用场景中的综合运用。每个案例包含需求分析、硬件配置、中断优先级设计、完整代码和设计要点。

### 20.1 案例1：高精度电机控制中断（1kHz PWM + ADC 同步采样）

**需求**：BLDC 电机 FOC 控制，1kHz PWM 开关频率，ADC 在 PWM 中点采样电流，避免开关噪声。

**设计要点**：
- TIM1 中心对齐 PWM，1kHz。
- ADC 由 TIM1 触发，在 PWM 计数到顶点时采样。
- ADC 完成中断中执行 FOC 算法（Clarke/Park/PI/SVPWM）。
- 优先级 0（最高），不调用 RTOS API。

```c
// Motor control: 1kHz center-aligned PWM + ADC sync sampling
void motor_control_init(void) {
    // === TIM1: center-aligned PWM, 1kHz ===
    __HAL_RCC_TIM1_CLK_ENABLE();
    TIM1->PSC = 0;                       // 168MHz
    TIM1->ARR = 168000 / 2 / 1000 - 1;   // Center-aligned: /2
    TIM1->CR1 = TIM_CR1_CMS_0 |          // Center-aligned mode 1
                TIM_CR1_CEN;
    // PWM channels (3-phase)
    TIM1->CCMR1 = TIM_CCMR1_OC1M_1 | TIM_CCMR1_OC1M_2 |  // PWM mode 1
                  TIM_CCMR1_OC2M_1 | TIM_CCMR1_OC2M_2;
    TIM1->CCMR2 = TIM_CCMR2_OC3M_1 | TIM_CCMR2_OC3M_2;
    TIM1->CCER = TIM_CCER_CC1E | TIM_CCER_CC2E | TIM_CCER_CC3E;
    // Trigger ADC on update event (PWM center = top of count)
    TIM1->CR2 = TIM_CR2_MMS_1;  // OC4REF as TRGO, or use update
    
    // === ADC1: triggered by TIM1, 3 channels (phase current) ===
    __HAL_RCC_ADC1_CLK_ENABLE();
    ADC1->CR1 = ADC_CR1_SCAN |          // Scan mode
                ADC_CR1_EOCIE;          // EOC interrupt
    ADC1->CR2 = ADC_CR2_DMA |           // DMA mode
                ADC_CR2_TSVREFE;        // Enable temp sensor
    // External trigger: TIM1 TRGO
    ADC1->CR2 |= (0b0100 << ADC_CR2_EXTSEL_Pos) |  // TIM1 TRGO
                 ADC_CR2_EXTTRIG;
    // Sequence: channel 1, 2, 3 (phase A, B, C current)
    ADC1->SQR1 = (3 - 1) << 20;  // 3 conversions
    ADC1->SQR3 = (1 << 0) | (2 << 5) | (3 << 10);
    ADC1->SMPR1 = (7 << 0) | (7 << 5) | (7 << 10);  // 480 cycles each
    
    HAL_NVIC_SetPriority(ADC_IRQn, 0, 0);  // Highest priority
    HAL_NVIC_EnableIRQ(ADC_IRQn);
    
    // Start PWM and ADC
    TIM1->BDTR = TIM_BDTR_MOE;  // Main output enable
    TIM1->CR1 |= TIM_CR1_CEN;
    ADC1->CR2 |= ADC_CR2_ADON;
}

// ADC ISR: runs FOC algorithm
volatile float g_i_a, g_i_b, g_i_c;
volatile float g_v_alpha, g_v_beta;
volatile float g_theta = 0;

void ADC_IRQHandler(void) {
    // Read 3 phase currents (DMA wrote them)
    g_i_a = (adc_buf[0] - 2048) * ADC_TO_AMP;
    g_i_b = (adc_buf[1] - 2048) * ADC_TO_AMP;
    g_i_c = (adc_buf[2] - 2048) * ADC_TO_AMP;
    
    // Clarke transform: 3-phase to 2-phase
    float i_alpha = g_i_a;
    float i_beta = (g_i_a + 2 * g_i_b) * 0.57735f;
    
    // Park transform: stationary to rotating frame
    float sin_t = sinf(g_theta);
    float cos_t = cosf(g_theta);
    float i_d = i_alpha * cos_t + i_beta * sin_t;
    float i_q = -i_alpha * sin_t + i_beta * cos_t;
    
    // PI controllers for Id, Iq
    float v_d = pi_controller_id(i_d_target - i_d);
    float v_q = pi_controller_iq(i_q_target - i_q);
    
    // Inverse Park
    g_v_alpha = v_d * cos_t - v_q * sin_t;
    g_v_beta = v_d * sin_t + v_q * cos_t;
    
    // SVPWM: calculate duty cycles
    uint32_t ccr_a, ccr_b, ccr_c;
    svpwm_calculate(g_v_alpha, g_v_beta, &ccr_a, &ccr_b, &ccr_c);
    TIM1->CCR1 = ccr_a;
    TIM1->CCR2 = ccr_b;
    TIM1->CCR3 = ccr_c;
    
    // Update theta for next cycle
    g_theta += 2 * 3.14159f * 50 / 1000;  // 50Hz at 1kHz
    if (g_theta > 2 * 3.14159f) g_theta -= 2 * 3.14159f;
}
```

### 20.2 案例2：多通道 UART DMA 接收中断处理

**需求**：4 个 UART 同时接收数据（不同协议），每个 UART 用 DMA + IDLE 中断。

```c
// Multi-channel UART DMA reception
typedef struct {
    USART_TypeDef *uart;
    DMA_Stream_TypeDef *dma;
    uint8_t buf[256];
    TaskHandle_t task;
    volatile uint16_t len;
} uart_dma_t;

static uart_dma_t g_uarts[4];

void uart_dma_init_all(void) {
    // Configure 4 UARTs with DMA
    uart_dma_config(0, USART1, DMA2_Stream2, DMA_CHANNEL_4);
    uart_dma_config(1, USART2, DMA1_Stream5, DMA_CHANNEL_4);
    uart_dma_config(2, USART3, DMA1_Stream1, DMA_CHANNEL_4);
    uart_dma_config(3, UART4,  DMA1_Stream2, DMA_CHANNEL_4);
}

void uart_dma_config(int idx, USART_TypeDef *u, DMA_Stream_TypeDef *d, uint32_t ch) {
    g_uarts[idx].uart = u;
    g_uarts[idx].dma = d;
    
    // Enable DMA for UART RX
    u->CR3 |= USART_CR3_DMAR;
    // Enable IDLE interrupt
    u->CR1 |= USART_CR1_IDLEIE;
    
    // Configure DMA
    d->CR &= ~DMA_SxCR_EN;
    d->PAR = (uint32_t)&u->DR;
    d->M0AR = (uint32_t)g_uarts[idx].buf;
    d->NDTR = sizeof(g_uarts[idx].buf);
    d->CR = (ch << DMA_SxCR_CHSEL_Pos) |
            DMA_SxCR_MINC | DMA_SxCR_CIRC |
            DMA_SxCR_HTIE | DMA_SxCR_TCIE | DMA_SxCR_EN;
}

// Generic UART ISR: handles IDLE, dispatches to per-uart handler
void uart_isr(int idx) {
    BaseType_t hpw = pdFALSE;
    uart_dma_t *u = &g_uarts[idx];
    
    if (u->uart->SR & USART_SR_IDLE) {
        (void)u->uart->DR;  // Clear IDLE
        // Calculate received length
        uint16_t remaining = u->dma->NDTR;
        u->len = sizeof(u->buf) - remaining;
        // Notify task
        xTaskNotifyFromISR(u->task, u->len, eSetValueWithOverwrite, &hpw);
    }
    portYIELD_FROM_ISR(hpw);
}

// Individual UART ISRs (thin wrappers)
void USART1_IRQHandler(void) { uart_isr(0); }
void USART2_IRQHandler(void) { uart_isr(1); }
void USART3_IRQHandler(void) { uart_isr(2); }
void UART4_IRQHandler(void)  { uart_isr(3); }

// Per-channel processing tasks
void uart_task(void *pv) {
    int idx = (int)pvParameters;
    uart_dma_t *u = &g_uarts[idx];
    uint32_t len;
    for (;;) {
        xTaskNotifyWait(0, 0xFFFFFFFF, &len, portMAX_DELAY);
        if (len > 0) {
            process_protocol(idx, u->buf, len);
        }
    }
}
```

### 20.3 案例3：CAN 总线中断驱动的消息调度

**需求**：CAN 总线接收多种 ID 的消息，按优先级分发到不同处理任务。

```c
// CAN message dispatch via interrupt
typedef enum {
    CAN_MSG_TYPE_CMD = 0x100,
    CAN_MSG_TYPE_STATUS = 0x200,
    CAN_MSG_TYPE_TELEMETRY = 0x300,
} can_msg_type_t;

static QueueHandle_t s_can_queues[3];

void can_dispatch_init(void) {
    s_can_queues[0] = xQueueCreate(16, sizeof(can_frame_t));  // CMD (high prio)
    s_can_queues[1] = xQueueCreate(32, sizeof(can_frame_t));  // STATUS
    s_can_queues[2] = xQueueCreate(64, sizeof(can_frame_t));  // TELEMETRY
    
    // Configure CAN filters: accept all, dispatch in ISR
    CAN1->FMR |= CAN_FMR_FINIT;
    CAN1->FA1R = 1;  // Enable filter 0
    CAN1->sFilterRegister[0].FR1 = 0;  // Accept all
    CAN1->sFilterRegister[0].FR2 = 0;
    CAN1->FMR &= ~CAN_FMR_FINIT;
    
    CAN1->IER |= CAN_IER_FMPIE0;  // FIFO0 message pending
    HAL_NVIC_SetPriority(CAN1_RX0_IRQn, 6, 0);
    HAL_NVIC_EnableIRQ(CAN1_RX0_IRQn);
}

void CAN1_RX0_IRQHandler(void) {
    BaseType_t hpw = pdFALSE;
    can_frame_t frame;
    
    while (CAN1->RF0R & CAN_RF0R_FMP0) {
        // Read message from FIFO
        frame.id = CAN1->sFIFOMailBox[0].RIR >> 21;
        frame.dlc = CAN1->sFIFOMailBox[0].RDTR & 0xF;
        frame.data[0] = CAN1->sFIFOMailBox[0].RDLR & 0xFF;
        frame.data[1] = (CAN1->sFIFOMailBox[0].RDLR >> 8) & 0xFF;
        // ... read all 8 bytes ...
        CAN1->RF0R |= CAN_RF0R_RFOM0;  // Release FIFO
        
        // Dispatch by ID range
        int queue_idx;
        if (frame.id >= 0x100 && frame.id < 0x200) queue_idx = 0;
        else if (frame.id >= 0x200 && frame.id < 0x300) queue_idx = 1;
        else queue_idx = 2;
        
        xQueueSendFromISR(s_can_queues[queue_idx], &frame, &hpw);
    }
    portYIELD_FROM_ISR(hpw);
}

// High-priority command task
void can_cmd_task(void *pv) {
    can_frame_t frame;
    for (;;) {
        xQueueReceive(s_can_queues[0], &frame, portMAX_DELAY);
        execute_can_command(&frame);
    }
}
```

### 20.4 案例4：音频 ADC/DMA 双缓冲中断处理

**需求**：48kHz 立体声音频采集，DMA 双缓冲，半传输和完成中断分别处理两个缓冲。

```c
// Audio ADC with DMA double buffering (48kHz stereo)
#define AUDIO_BUF_SIZE  512  // Samples per half-buffer (256 stereo frames)
static int16_t g_audio_buf[AUDIO_BUF_SIZE * 2];  // 2 half-buffers

static StreamBufferHandle_t s_audio_stream;

void audio_adc_init(void) {
    s_audio_stream = xStreamBufferCreate(AUDIO_BUF_SIZE * 4, AUDIO_BUF_SIZE * 2);
    
    // Configure timer for 48kHz trigger (or use I2S clock)
    TIM6->PSC = 0;
    TIM6->ARR = (SystemCoreClock / 48000) - 1;
    TIM6->CR2 = TIM_CR2_MMS_1;  // Update event as TRGO
    TIM6->CR1 = TIM_CR1_CEN;
    
    // ADC triggered by TIM6
    ADC1->CR2 = (0b0110 << ADC_CR2_EXTSEL_Pos) |  // TIM6 TRGO
                ADC_CR2_EXTTRIG | ADC_CR2_DMA | ADC_CR2_DDS | ADC_CR2_ADON;
    ADC1->CR1 = ADC_CR1_SCAN | ADC_CR1_DISCEN;
    // 2 channels: left, right
    ADC1->SQR1 = (2 - 1) << 20;
    ADC1->SQR3 = (AUDIO_L_CH << 0) | (AUDIO_R_CH << 5);
    
    // DMA: circular, half-word, double buffer
    DMA2_Stream0->PAR = (uint32_t)&ADC1->DR;
    DMA2_Stream0->M0AR = (uint32_t)g_audio_buf;
    DMA2_Stream0->NDTR = AUDIO_BUF_SIZE * 2;
    DMA2_Stream0->CR = DMA_SxCR_CHSEL_0 |
                       DMA_SxCR_MSIZE_0 | DMA_SxCR_PSIZE_0 |
                       DMA_SxCR_MINC | DMA_SxCR_CIRC |
                       DMA_SxCR_HTIE | DMA_SxCR_TCIE | DMA_SxCR_EN;
    
    HAL_NVIC_SetPriority(DMA2_Stream0_IRQn, 5, 0);
    HAL_NVIC_EnableIRQ(DMA2_Stream0_IRQn);
}

void DMA2_Stream0_IRQHandler(void) {
    BaseType_t hpw = pdFALSE;
    if (DMA2->LISR & DMA_LISR_HTIF0) {
        DMA2->LIFCR = DMA_LIFCR_CHTIF0;
        // First half ready [0..AUDIO_BUF_SIZE-1]
        xStreamBufferSendFromISR(s_audio_stream, g_audio_buf,
                                 AUDIO_BUF_SIZE * 2, &hpw);
    }
    if (DMA2->LISR & DMA_LISR_TCIF0) {
        DMA2->LIFCR = DMA_LIFCR_CTCIF0;
        // Second half ready [AUDIO_BUF_SIZE..2*AUDIO_BUF_SIZE-1]
        xStreamBufferSendFromISR(s_audio_stream,
                                 g_audio_buf + AUDIO_BUF_SIZE,
                                 AUDIO_BUF_SIZE * 2, &hpw);
    }
    portYIELD_FROM_ISR(hpw);
}

// Audio processing task
void audio_task(void *pv) {
    int16_t frame[AUDIO_BUF_SIZE];
    for (;;) {
        size_t got = xStreamBufferReceive(s_audio_stream, frame,
                                          sizeof(frame), portMAX_DELAY);
        if (got > 0) {
            // Process audio: filter, FFT, encode
            audio_process(frame, got / 2);
        }
    }
}
```

### 20.5 案例5：外部按钮消抖中断 + 软件状态机

**需求**：4 个按钮通过 EXTI 中断检测，软件消抖，状态机处理短按/长按/双击。

```c
// Button debouncing via EXTI + software state machine
#define NUM_BUTTONS 4
#define DEBOUNCE_MS 20
#define LONG_PRESS_MS 1000
#define DOUBLE_CLICK_MS 300

typedef enum {
    BTN_STATE_IDLE,
    BTN_STATE_PRESSED,
    BTN_STATE_RELEASED,
    BTN_STATE_LONG_PRESS
} btn_state_t;

typedef struct {
    uint8_t pin;
    uint8_t exti_line;
    btn_state_t state;
    uint32_t press_tick;
    uint32_t release_tick;
    uint8_t click_count;
} button_t;

static button_t g_buttons[NUM_BUTTONS];
static TaskHandle_t s_button_task;

void button_init_all(void) {
    // Configure 4 buttons on PA0, PA1, PA2, PA3
    GPIO_InitTypeDef gi = {0};
    gi.Mode = GPIO_MODE_IT_FALLING;  // Active low (pull-up)
    gi.Pull = GPIO_PULLUP;
    
    const uint8_t pins[NUM_BUTTONS] = {GPIO_PIN_0, GPIO_PIN_1,
                                        GPIO_PIN_2, GPIO_PIN_3};
    for (int i = 0; i < NUM_BUTTONS; i++) {
        gi.Pin = pins[i];
        HAL_GPIO_Init(GPIOA, &gi);
        g_buttons[i].pin = pins[i];
        g_buttons[i].exti_line = i;
        g_buttons[i].state = BTN_STATE_IDLE;
    }
    
    HAL_NVIC_SetPriority(EXTI0_IRQn, 12, 0);
    HAL_NVIC_SetPriority(EXTI1_IRQn, 12, 0);
    HAL_NVIC_SetPriority(EXTI2_IRQn, 12, 0);
    HAL_NVIC_SetPriority(EXTI3_IRQn, 12, 0);
    HAL_NVIC_EnableIRQ(EXTI0_IRQn);
    HAL_NVIC_EnableIRQ(EXTI1_IRQn);
    HAL_NVIC_EnableIRQ(EXTI2_IRQn);
    HAL_NVIC_EnableIRQ(EXTI3_IRQn);
}

// ISR: minimal work, just record event and notify task
void button_isr(uint8_t idx) {
    BaseType_t hpw = pdFALSE;
    EXTI->PR = (1 << idx);  // Clear pending
    
    uint32_t now = HAL_GetTick();
    if (now - g_buttons[idx].release_tick > DEBOUNCE_MS) {
        // Valid press (after debounce)
        g_buttons[idx].press_tick = now;
        g_buttons[idx].state = BTN_STATE_PRESSED;
        vTaskNotifyGiveFromISR(s_button_task, &hpw);
    }
    portYIELD_FROM_ISR(hpw);
}

void EXTI0_IRQHandler(void) { button_isr(0); }
void EXTI1_IRQHandler(void) { button_isr(1); }
void EXTI2_IRQHandler(void) { button_isr(2); }
void EXTI3_IRQHandler(void) { button_isr(3); }

// Button state machine task
void button_task(void *pv) {
    for (;;) {
        // Wait for any button event
        xTaskNotifyWait(0, 0xFFFFFFFF, NULL, pdMS_TO_TICKS(10));
        
        uint32_t now = HAL_GetTick();
        for (int i = 0; i < NUM_BUTTONS; i++) {
            button_t *b = &g_buttons[i];
            
            switch (b->state) {
                case BTN_STATE_PRESSED:
                    if (HAL_GPIO_ReadPin(GPIOA, b->pin) == GPIO_PIN_SET) {
                        // Released
                        if (now - b->press_tick < LONG_PRESS_MS) {
                            // Short press
                            b->click_count++;
                            b->release_tick = now;
                            b->state = BTN_STATE_RELEASED;
                            if (b->click_count == 1) {
                                // Wait for potential double click
                            } else if (b->click_count == 2) {
                                button_event(i, EVENT_DOUBLE_CLICK);
                                b->click_count = 0;
                                b->state = BTN_STATE_IDLE;
                            }
                        }
                    } else if (now - b->press_tick >= LONG_PRESS_MS) {
                        // Long press detected
                        button_event(i, EVENT_LONG_PRESS);
                        b->state = BTN_STATE_LONG_PRESS;
                    }
                    break;
                    
                case BTN_STATE_RELEASED:
                    // Check for double click timeout
                    if (now - b->release_tick > DOUBLE_CLICK_MS) {
                        if (b->click_count == 1) {
                            button_event(i, EVENT_SHORT_PRESS);
                        }
                        b->click_count = 0;
                        b->state = BTN_STATE_IDLE;
                    }
                    break;
                    
                case BTN_STATE_LONG_PRESS:
                    if (HAL_GPIO_ReadPin(GPIOA, b->pin) == GPIO_PIN_SET) {
                        // Long press released
                        b->state = BTN_STATE_IDLE;
                        b->click_count = 0;
                    }
                    break;
                    
                default:
                    break;
            }
        }
    }
}

// Button event callback
void button_event(uint8_t btn_idx, uint8_t event) {
    const char *event_names[] = {"SHORT", "LONG", "DOUBLE"};
    printf("Button %d: %s\n", btn_idx, event_names[event]);
    // Dispatch to application logic
    switch (event) {
        case EVENT_SHORT_PRESS:
            app_handle_short_press(btn_idx);
            break;
        case EVENT_LONG_PRESS:
            app_handle_long_press(btn_idx);
            break;
        case EVENT_DOUBLE_CLICK:
            app_handle_double_click(btn_idx);
            break;
    }
}
```

---

## 附录 Q：Cortex-M 异常向量表完整列表

下表列出 Cortex-M 完整异常向量表（系统异常 + 部分外部中断示例），含中断号、名称、优先级默认值。

| IRQn | 异常号 | 向量偏移 | 名称 | 优先级 | 可配置 | 说明 |
|------|--------|---------|------|--------|--------|------|
| - | 1 | 0x04 | Reset | -3（固定） | 否 | 复位，最高优先级 |
| - | 2 | 0x08 | NMI | -2（固定） | 否 | 不可屏蔽中断 |
| - | 3 | 0x0C | HardFault | -1（固定） | 否 | 硬件故障 |
| - | 4 | 0x10 | MemManage | 0 | 是 | MPU 违规 |
| - | 5 | 0x14 | BusFault | 0 | 是 | 总线错误 |
| - | 6 | 0x18 | UsageFault | 0 | 是 | 用法错误 |
| - | 7-10 | 0x1C-0x28 | Reserved | - | - | 保留 |
| - | 11 | 0x2C | SVCall | 0 | 是 | 系统服务调用 |
| - | 12 | 0x30 | Debug Monitor | 0 | 是 | 调试监控 |
| - | 13 | 0x34 | Reserved | - | - | 保留 |
| - | 14 | 0x38 | PendSV | 0xFF | 是 | 可挂起系统调用（RTOS 切换） |
| - | 15 | 0x3C | SysTick | 0xFF | 是 | 系统节拍定时器 |
| 0 | 16 | 0x40 | WWDG_IRQn | 0 | 是 | 窗口看门狗 |
| 1 | 17 | 0x44 | PVD_IRQn | 0 | 是 | 电源电压检测 |
| 2 | 18 | 0x48 | TAMP_STAMP_IRQn | 0 | 是 | 篡改/时间戳 |
| 3 | 19 | 0x4C | RTC_WKUP_IRQn | 0 | 是 | RTC 唤醒 |
| 4 | 20 | 0x50 | FLASH_IRQn | 0 | 是 | Flash 全局中断 |
| 5 | 21 | 0x54 | RCC_IRQn | 0 | 是 | 时钟控制 |
| 6 | 22 | 0x58 | EXTI0_IRQn | 0 | 是 | 外部中断线 0 |
| 7 | 23 | 0x5C | EXTI1_IRQn | 0 | 是 | 外部中断线 1 |
| 8 | 24 | 0x60 | EXTI2_IRQn | 0 | 是 | 外部中断线 2 |
| 9 | 25 | 0x64 | EXTI3_IRQn | 0 | 是 | 外部中断线 3 |
| 10 | 26 | 0x68 | EXTI4_IRQn | 0 | 是 | 外部中断线 4 |
| 11 | 27 | 0x6C | DMA1_Stream0_IRQn | 0 | 是 | DMA1 流 0 |
| 12 | 28 | 0x70 | DMA1_Stream1_IRQn | 0 | 是 | DMA1 流 1 |
| 13 | 29 | 0x74 | DMA1_Stream2_IRQn | 0 | 是 | DMA1 流 2 |
| 14 | 30 | 0x78 | DMA1_Stream3_IRQn | 0 | 是 | DMA1 流 3 |
| 15 | 31 | 0x7C | DMA1_Stream4_IRQn | 0 | 是 | DMA1 流 4 |
| ... | ... | ... | ... | 0 | 是 | 更多外部中断 |
| 23 | 39 | 0x9C | EXTI9_5_IRQn | 0 | 是 | 外部中断线 5-9 |
| ... | ... | ... | ... | 0 | 是 | |
| 40 | 56 | 0xE0 | EXTI15_10_IRQn | 0 | 是 | 外部中断线 10-15 |

注意：
- 优先级 -3/-2/-1 是固定值，无法通过 NVIC_SetPriority 修改。
- 默认优先级 0 是最高可编程优先级，0xFF 是最低（对 4 位实现截断为 0xF0 = 15）。
- 外部中断的 IRQn 从 0 开始，异常号 = IRQn + 16。
- 向量偏移 = 异常号 × 4。

## 附录 R：NVIC 寄存器完整位域表

### ISER0-7（中断使能置位寄存器）

| 位 | 31 | 30 | ... | 1 | 0 |
|----|----|----|-----|---|---|
| 含义 | IRQ31 | IRQ30 | ... | IRQ1 | IRQ0 |

- 地址：0xE000E100 + 4×n（n=0-7）
- 写 1 使能对应 IRQ，写 0 无效。
- 读返回当前使能状态。
- CMSIS：`NVIC_EnableIRQ(IRQn_Type)`

### ICER0-7（中断使能清除寄存器）

| 位 | 31 | 30 | ... | 1 | 0 |
|----|----|----|-----|---|---|
| 含义 | IRQ31 | IRQ30 | ... | IRQ1 | IRQ0 |

- 地址：0xE000E180 + 4×n
- 写 1 禁能对应 IRQ，写 0 无效。
- CMSIS：`NVIC_DisableIRQ(IRQn_Type)`

### ISPR0-7（中断挂起置位寄存器）

| 位 | 31 | 30 | ... | 1 | 0 |
|----|----|----|-----|---|---|
| 含义 | IRQ31 | IRQ30 | ... | IRQ1 | IRQ0 |

- 地址：0xE000E200 + 4×n
- 写 1 挂起对应 IRQ（软件触发）。
- CMSIS：`NVIC_SetPendingIRQ(IRQn_Type)`

### ICPR0-7（中断挂起清除寄存器）

| 位 | 31 | 30 | ... | 1 | 0 |
|----|----|----|-----|---|---|
| 含义 | IRQ31 | IRQ30 | ... | IRQ1 | IRQ0 |

- 地址：0xE000E280 + 4×n
- 写 1 清除挂起状态。
- CMSIS：`NVIC_ClearPendingIRQ(IRQn_Type)`

### IABR0-7（中断活跃位寄存器，只读）

| 位 | 31 | 30 | ... | 1 | 0 |
|----|----|----|-----|---|---|
| 含义 | IRQ31 | IRQ30 | ... | IRQ1 | IRQ0 |

- 地址：0xE000E300 + 4×n
- bit=1 表示对应 IRQ 正在执行（活跃）。
- 硬件在 ISR 进入时置位，退出时清除。
- CMSIS：`NVIC_GetActive(IRQn_Type)`

### IP0-239（中断优先级寄存器）

每个 IRQ 占 1 字节（8 位），但仅高 N 位有效（N = 实现的优先级位数）。

| 字节 | bit 7 | bit 6 | bit 5 | bit 4 | bit 3 | bit 2 | bit 1 | bit 0 |
|------|-------|-------|-------|-------|-------|-------|-------|-------|
| 4 位实现 | PRIO[3] | PRIO[2] | PRIO[1] | PRIO[0] | 0 | 0 | 0 | 0 |
| 3 位实现 | PRIO[2] | PRIO[1] | PRIO[0] | 0 | 0 | 0 | 0 | 0 |
| 8 位实现 | PRIO[7] | PRIO[6] | PRIO[5] | PRIO[4] | PRIO[3] | PRIO[2] | PRIO[1] | PRIO[0] |

- 地址：0xE000E400 + IRQn
- 数值越小优先级越高。
- 对 4 位实现，写入 0xFF 会被截断为 0xF0（即优先级 15）。
- CMSIS：`NVIC_SetPriority(IRQn_Type, uint32_t priority)`

### AIRCR（应用中断与复位控制寄存器）

| 位 | 名称 | 作用 |
|----|------|------|
| 31:16 | VECTKEY | 写密钥 0x5FA 才能写此寄存器 |
| 15 | ENDIANESS | 端模式（0=小端，1=大端） |
| 10:8 | PRIGROUP | 优先级分组（决定抢占/子优先级位数分配） |
| 2 | SYSRESETREQ | 系统复位请求 |
| 1 | VECTCLRACTIVE | 清除活跃状态（调试用） |

## 附录 S：汇编语言中断处理模板

### S.1 PendSV 上下文切换（FreeRTOS 风格）

```asm
// PendSV handler for RTOS context switch (Cortex-M4F)
// Saves current task context, selects next task, restores its context
__attribute__((naked)) void PendSV_Handler(void) {
    __asm volatile(
        "    .syntax unified               \n"
        "    mrs r0, psp                    \n"  // Get process stack pointer
        "    isb                            \n"
        "                                       \n"  // Check if LR indicates FPU context
        "    tst r14, #0x10                 \n"  // bit 4 of EXC_RETURN
        "    it eq                          \n"
        "    vstmdbeq r0!, {s16-s31}        \n"  // Save FP high regs if FPU used
        "                                       \n"
        "    ldr r3, =pxCurrentTCB          \n"  // Current task TCB pointer
        "    ldr r2, [r3]                   \n"  // r2 = &TCB
        "                                       \n"
        "    stmdb r0!, {r4-r11, r14}       \n"  // Save core regs + LR
        "    str r0, [r2]                   \n"  // Save new SP to TCB
        "                                       \n"
        "    stmdb sp!, {r0, r3}            \n"  // Save r0, r3 on MSP
        "    bl vTaskSwitchContext          \n"  // Select next task
        "    ldmia sp!, {r0, r3}            \n"  // Restore r0, r3
        "                                       \n"
        "    ldr r1, [r3]                   \n"  // r1 = &nextTCB
        "    ldr r0, [r1]                   \n"  // r0 = next SP
        "                                       \n"
        "    ldmia r0!, {r4-r11, r14}       \n"  // Restore core regs + LR
        "    tst r14, #0x10                 \n"  // Check FPU context
        "    it eq                          \n"
        "    vldmiaeq r0!, {s16-s31}        \n"  // Restore FP high regs
        "                                       \n"
        "    msr psp, r0                    \n"  // Set PSP for next task
        "    isb                            \n"
        "    bx r14                         \n"  // Return to next task
    );
}
```

### S.2 SVC 处理程序

```asm
// SVC handler: extract SVC number and dispatch
__attribute__((naked)) void SVC_Handler(void) {
    __asm volatile(
        "    tst lr, #4                     \n"  // Check which stack
        "    ite eq                         \n"
        "    mrseq r0, msp                  \n"  // MSP if Handler mode
        "    mrsne r0, psp                  \n"  // PSP if Thread mode
        "    ldr r1, [r0, #24]              \n"  // Get PC from stack frame
        "    ldrb r1, [r1, #-2]             \n"  // SVC number (byte before PC)
        "    b SVC_Handler_C                \n"  // Call C handler with (stack, svc_num)
    );
}

// C handler receives stack frame and SVC number
void SVC_Handler_C(uint32_t *stack, uint8_t svc_num) {
    switch (svc_num) {
        case 0:  // SVC_0: start scheduler
            vPortStartFirstTask();
            break;
        case 1:  // SVC_1: yield to another task
            // Trigger PendSV for context switch
            SCB->ICSR |= SCB_ICSR_PENDSVSET_Msk;
            break;
        case 2:  // SVC_2: raise privilege
            // (for non-secure to secure transition)
            break;
        default:
            // Invalid SVC
            break;
    }
}
```

### S.3 硬件压栈后的栈帧结构

```asm
// Stack frame after hardware stacking (Cortex-M4 with FPU)
// MSP or PSP points to:
//   Offset 0x00: R0
//   Offset 0x04: R1
//   Offset 0x08: R2
//   Offset 0x0C: R3
//   Offset 0x10: R12
//   Offset 0x14: LR (R14)
//   Offset 0x18: PC (R15) - return address
//   Offset 0x1C: xPSR
//   (If FPU context saved, additionally:)
//   Offset 0x20: S0
//   Offset 0x24: S1
//   ...
//   Offset 0x5C: S15
//   Offset 0x60: FPCSR
//   Offset 0x64: Reserved (alignment)

// To extract fault PC from stack frame in HardFault handler:
//   ldr r0, [sp, #0x18]  // PC at fault
//   ldr r1, [sp, #0x1C]  // xPSR at fault
```

## 附录 T：中断相关 CMSIS API 速查表

### NVIC 操作函数

| 函数 | 原型 | 说明 |
|------|------|------|
| NVIC_EnableIRQ | void NVIC_EnableIRQ(IRQn_Type IRQn) | 使能中断 |
| NVIC_DisableIRQ | void NVIC_DisableIRQ(IRQn_Type IRQn) | 禁能中断 |
| NVIC_GetPendingIRQ | uint32_t NVIC_GetPendingIRQ(IRQn_Type IRQn) | 获取挂起状态 |
| NVIC_SetPendingIRQ | void NVIC_SetPendingIRQ(IRQn_Type IRQn) | 设置挂起 |
| NVIC_ClearPendingIRQ | void NVIC_ClearPendingIRQ(IRQn_Type IRQn) | 清除挂起 |
| NVIC_GetActive | uint32_t NVIC_GetActive(IRQn_Type IRQn) | 获取活跃状态 |
| NVIC_SetPriority | void NVIC_SetPriority(IRQn_Type IRQn, uint32_t priority) | 设置优先级 |
| NVIC_GetPriority | uint32_t NVIC_GetPriority(IRQn_Type IRQn) | 获取优先级 |
| NVIC_SetPriorityGrouping | void NVIC_SetPriorityGrouping(uint32_t PriorityGroup) | 设置优先级分组 |
| NVIC_GetPriorityGrouping | uint32_t NVIC_GetPriorityGrouping(void) | 获取优先级分组 |
| NVIC_SystemReset | void NVIC_SystemReset(void) | 系统复位 |

### 系统控制函数

| 函数 | 原型 | 说明 |
|------|------|------|
| __enable_irq | void __enable_irq(void) | 清除 PRIMASK |
| __disable_irq | void __disable_irq(void) | 设置 PRIMASK |
| __get_PRIMASK | uint32_t __get_PRIMASK(void) | 读 PRIMASK |
| __set_PRIMASK | void __set_PRIMASK(uint32_t priMask) | 写 PRIMASK |
| __get_BASEPRI | uint32_t __get_BASEPRI(void) | 读 BASEPRI |
| __set_BASEPRI | void __set_BASEPRI(uint32_t value) | 写 BASEPRI |
| __get_FAULTMASK | uint32_t __get_FAULTMASK(void) | 读 FAULTMASK |
| __set_FAULTMASK | void __set_FAULTMASK(uint32_t faultMask) | 写 FAULTMASK |
| __get_MSP | uint32_t __get_MSP(void) | 读 MSP |
| __set_MSP | void __set_MSP(uint32_t topOfMainStack) | 写 MSP |
| __get_PSP | uint32_t __get_PSP(void) | 读 PSP |
| __set_PSP | void __set_PSP(uint32_t topOfProcStack) | 写 PSP |
| __get_CONTROL | uint32_t __get_CONTROL(void) | 读 CONTROL |
| __set_CONTROL | void __set_CONTROL(uint32_t control) | 写 CONTROL |

### 内存屏障与同步

| 函数 | 原型 | 说明 |
|------|------|------|
| __ISB | void __ISB(void) | 指令同步屏障 |
| __DSB | void __DSB(void) | 数据同步屏障 |
| __DMB | void __DMB(void) | 数据内存屏障 |
| __WFI | void __WFI(void) | 等待中断 |
| __WFE | void __WFE(void) | 等待事件 |
| __SEV | void __SEV(void) | 发送事件 |

### 杂项内联函数

| 函数 | 原型 | 说明 |
|------|------|------|
| __NOP | void __NOP(void) | 空操作 |
| __CLZ | uint8_t __CLZ(uint32_t value) | 前导零计数 |
| __RBIT | uint32_t __RBIT(uint32_t value) | 位反转 |
| __REV | uint32_t __REV(uint32_t value) | 字节反转（32 位） |
| __REV16 | uint32_t __REV16(uint32_t value) | 字节反转（16 位） |
| __LDREXB | uint8_t __LDREXB(volatile uint8_t *addr) | 独占加载字节 |
| __STREXB | uint32_t __STREXB(uint8_t value, volatile uint8_t *addr) | 独占存储字节 |
| __get_LR | uint32_t __get_LR(void) | 读链接寄存器 |

## 附录 U：常见编译器中断属性对比

不同编译器声明 ISR 的语法不同，下表对比三大主流编译器：

| 编译器 | ISR 声明语法 | 说明 |
|--------|-------------|------|
| GCC (arm-none-eabi-gcc) | `void ISR(void) __attribute__((interrupt("IRQ")))` | 实际上 Cortex-M 不需要此属性（硬件自动压栈） |
| Keil ARMCC | `void __irq ISR(void)` | 传统声明，Cortex-M 上可选 |
| IAR | `__irq void ISR(void)` | IAR 的中断声明 |
| MSVC (无) | 不适用 | 不支持 ARM |

**重要**：Cortex-M 的硬件自动压栈机制使得 ISR 在 C 语言中**无需特殊声明**。普通 C 函数即可作为 ISR：

```c
// Cortex-M: plain C function works as ISR (no special attribute needed)
void USART1_IRQHandler(void) {
    if (USART1->SR & USART_SR_RXNE) {
        uint8_t b = USART1->DR;
        // ...
    }
}
```

但在某些情况下需要特殊属性：

```c
// 1. Naked function for custom stack frame handling (HardFault)
__attribute__((naked)) void HardFault_Handler(void) {
    __asm volatile("tst lr, #4\nite eq\nmrseq r0, msp\nmrsne r0, psp\nb HardFault_C");
}

// 2. Place ISR in SRAM for zero wait state (critical timing)
__attribute__((section(".ramfunc"), noinline))
void TIM1_UP_IRQHandler(void) { /* ... */ }

// 3. Place ISR in ITCM (Cortex-M7)
__attribute__((section(".itcm")))
void motor_control_isr(void) { /* ... */ }

// 4. ARMCC (Keil) specific
__irq void HardFault_Handler(void) { /* ... */ }
// In Keil, __irq tells compiler this is an ISR (affects some optimizations)

// 5. IAR specific
#pragma vector=USART1_IRQn
__interrupt void USART1_IRQHandler(void) { /* ... */ }
```

GCC `__attribute__((interrupt))` 在 Cortex-M 上**不推荐使用**，因为它会生成额外的压栈/出栈代码（软件压栈），与硬件自动压栈重复，增加延迟。Cortex-M 的 ABI 设计就是让 ISR 与普通函数一致。

各编译器中断属性效果对比：

| 属性 | GCC | Keil (__irq) | IAR (__interrupt) | Cortex-M 需要？ |
|------|-----|-------------|-------------------|----------------|
| 软件压栈 R4-R11 | interrupt 属性会 | __irq 会 | __interrupt 会 | 否（硬件已压栈） |
| 返回指令 | BX LR | SUBS PC, LR, #4 | BX LR | BX LR 即可 |
| 对 Cortex-M 影响 | 增加延迟 | 增加延迟 | 增加延迟 | 不推荐 |
| 适用场景 | ARM7TDMI 等旧架构 | 传统 ARM | 传统 ARM | Cortex-M 不需要 |

**结论**：在 Cortex-M 上，直接写普通 C 函数作为 ISR，函数名与启动文件向量表中的名称一致即可。避免使用 `__irq` / `interrupt` 属性，除非有特殊需求（如 naked 函数）。

---

## 文档扩展说明

本文档在原 13 章 + 附录 A-P 的基础上，扩展了以下内容：

- **第 14 章**：Cortex-M 调试与追踪系统详解（SWD/JTAG、ITM、DWT、ETM、MTB、OpenOCD/ST-Link/J-Link 配置）
- **第 15 章**：RTOS 中断设计模式（BASEPRI/PRIMASK 临界区、SYSCALL_PRIORITY、FromISR API、优先级继承、UART 完整驱动）
- **第 16 章**：低功耗中断设计（WFI/WFE、Sleep/Stop/Standby、RTC 闹钟唤醒、EXTI 唤醒、LPTIM、功耗测量优化）
- **第 17 章**：安全关键系统中断设计（HardFault 恢复策略、IWDG/WWDG、安全状态机、故障注入测试、ISO 26262 ASIL 等级）
- **第 18 章**：中断性能基准测试（GPIO/定时器/DWT 三种测量法、各 Cortex-M 核心延迟对比表、ISR 执行时间测量、抖动分析、Cache/ITCM 影响）
- **第 19 章**：高级中断控制器 NVIC 扩展（TrustZone 安全扩展、ITNS 中断目标配置、SAU 配置、SG 跨世界调用、多核中断路由）
- **第 20 章**：实战案例集（电机控制 FOC、多通道 UART DMA、CAN 消息调度、音频双缓冲、按钮消抖状态机）
- **附录 Q-U**：异常向量表完整列表、NVIC 寄存器位域表、汇编中断处理模板（PendSV/SVC）、CMSIS API 速查、编译器中断属性对比

扩展后文档覆盖了 PRIGROUP 优先级分组、12 周期中断延迟、尾链（Tail-Chaining）与迟来（Late-Arrival）优化、抢占优先级与子优先级设计等核心概念，并包含大量可直接复用的 C 代码与汇编示例，适合作为嵌入式实时系统中断开发的完整参考手册。



