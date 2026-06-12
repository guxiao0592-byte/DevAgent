# 简易银行账户类

## 类图描述
设计并实现一个 BankAccount 类，包含以下结构和行为：

### 属性
- account_id: 字符串，唯一标识
- owner: 字符串，账户持有人
- balance: 浮点数，余额（默认0）
- transactions: 列表，交易记录

### 方法
- deposit(amount): 存款，金额必须大于0
- withdraw(amount): 取款，余额不足时抛出异常
- get_balance(): 返回当前余额
- get_transaction_history(): 返回交易记录
- transfer(target_account, amount): 转账到目标账户

### 约束
- 不允许透支（余额不能为负数）
- 存款金额必须大于0
- 取款金额必须大于0且不超过余额
- 转账金额必须大于0且不超过源账户余额
