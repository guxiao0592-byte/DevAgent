# 软件需求规格说明书 (SRS)

> 基于 IEEE 830-1998 标准

## 文档控制

| 字段 | 值 |
|------|-----|
| 文档 ID | SRS-User_Authentication_System-v1.0 |
| 版本 | 1.0 |
| 日期 | 2026-06-12 |
| 作者 | DevAgent (AI-assisted) |
| 状态 | Draft |

## 修订历史

| 版本 | 日期 | 作者 | 变更说明 |
|------|------|------|---------|
| 1.0 | 2026-06-12 | DevAgent | Initial requirements specification |

# 引言


## 1.1 目的

本文档定义了 **User Authentication System** 的软件需求规格。目标读者包括开发团队、测试团队、项目经理和利益相关者。

**项目描述**: A secure user authentication system with registration, login, JWT token management, and role-based access control, built with FastAPI and SQLAlchemy.

## 1.2 范围

本文档涵盖 User Authentication System 的完整功能需求、非功能需求、用例规格和领域模型。

## 1.3 定义、缩略语与术语表

## 术语表与缩略语

| 术语 | 定义 |
|------|------|
| **JWT** | JSON Web Token — compact, URL-safe token for authentication. |
| **bcrypt** | Password hashing function with adaptive work factor. |
| **Refresh Token** | Long-lived token used to obtain new access tokens. |
| **Access Token** | Short-lived token used to access protected resources. |
| **RBAC** | Role-Based Access Control. |

## 1.4 参考文献

- IEEE 830-1998: Recommended Practice for Software Requirements Specifications
- 项目输入文档 (requirements.md)

## 1.5 概述

本文档按以下结构组织:
- **第2章**: 总体描述 — 产品视角、功能概述、用户特征、约束与假设
- **第3章**: 具体需求 — 外部接口、功能需求(FR)、非功能需求(NFR)、安全需求、性能需求
- **附录**: 术语表、需求追溯矩阵(RTM)

# 总体描述


## 2.1 产品视角

**User Authentication System** 是一个 Web API 服务。

**目标用户**: Application developers integrating authentication into their services

**业务目标**:
- Provide secure user registration and login
- Implement role-based access control
- Ensure high test coverage and security

## 2.2 产品功能概述

系统包含 **5** 个功能需求，按优先级分布:

| 优先级 | 数量 |
|--------|------|
| Critical | 3 |
| High | 2 |
| Medium | 0 |
| Low | 0 |

## 2.3 用户特征

| Actor ID | 角色 | 描述 | 目标 |
|----------|------|------|------|
| ACT-01 | User | End user who registers and logs in | Register account, Log in, Refresh token |
| ACT-02 | Admin | Administrator who manages roles and users | Manage roles, Assign roles to users |
| ACT-03 | System | Automated processes for token validation and password hashin | Validate JWT, Hash passwords, Enforce access control |

## 2.4 约束

- **technical**: Must use Python 3.9+, FastAPI, SQLAlchemy, and pytest.

- **technical**: Development database: SQLite; Production database: PostgreSQL.

- **technical**: Authentication must use JWT (access token) and bcrypt for password hashing.

- **business**: System must support at least 1000 concurrent users.

## 2.5 假设与依赖

- Email service for verification is out of scope.

- User roles are predefined (admin, user) and can be extended.

- Token revocation is handled via database blacklist or refresh token deletion.

# 具体需求


## 3.1 领域模型

共 **3** 个领域实体

### User

- **描述**: Represents a registered user in the system

| 属性 | 类型 | 约束 | 描述 |
|------|------|------|------|
| id | `int` | PK, auto-increment | Unique identifier |
| email | `string` | unique, not null, max 255 chars | User email address |
| password_hash | `string` | not null | Bcrypt hash of password |
| role | `string` | not null, default 'user' | User role (e.g., admin, user) |
| is_active | `boolean` | default true | Whether the user account is active |
| created_at | `datetime` | not null, auto-set | Timestamp of account creation |
| updated_at | `datetime` | auto-update | Timestamp of last update |

### Role

- **描述**: Defines a role with specific permissions

| 属性 | 类型 | 约束 | 描述 |
|------|------|------|------|
| id | `int` | PK, auto-increment | Unique identifier |
| name | `string` | unique, not null, max 50 chars | Role name |
| description | `string` | max 255 chars | Role description |

**关系**:

- one-to-many → **User**: A role can be assigned to many users

### RefreshToken

- **描述**: Stores refresh tokens for JWT token refresh

| 属性 | 类型 | 约束 | 描述 |
|------|------|------|------|
| id | `int` | PK, auto-increment | Unique identifier |
| token | `string` | unique, not null | The refresh token value |
| user_id | `int` | FK, not null | Foreign key to User |
| expires_at | `datetime` | not null | Expiration timestamp |
| created_at | `datetime` | not null, auto-set | Timestamp of creation |

**关系**:

- many-to-one → **User**: A refresh token belongs to a user


## 3.2 功能需求 (FR)

### FR-01 🔴 User Registration

- **描述**: Allow a new user to register with email and password. Password must be hashed with bcrypt before storage.

- **优先级**: CRITICAL

- **参与者**: ACT-01

**验收标准**:

- [ ] AC1: User can register with a valid email and password (min 8 chars, at least one uppercase, one lowercase, one digit).

- [ ] AC2: Duplicate email registration returns error 409.

- [ ] AC3: Password is stored as bcrypt hash with work factor >= 12.

- [ ] AC4: On success, returns 201 with user ID and email.

### FR-02 🔴 User Login

- **描述**: Authenticate user with email and password, return JWT access token and refresh token.

- **优先级**: CRITICAL

- **参与者**: ACT-01

- **依赖**: FR-01

**验收标准**:

- [ ] AC1: Valid credentials return 200 with access token (expiry 15 min) and refresh token (expiry 7 days).

- [ ] AC2: Invalid email or password returns 401.

- [ ] AC3: Inactive user account returns 403.

### FR-03 🔴 Password Encryption with bcrypt

- **描述**: All passwords must be hashed using bcrypt with a work factor of at least 12 before storage.

- **优先级**: CRITICAL

- **参与者**: ACT-03

- **依赖**: FR-01

**验收标准**:

- [ ] AC1: Password hash is generated using bcrypt with work factor >= 12.

- [ ] AC2: Plaintext password is never stored or logged.

- [ ] AC3: Password verification uses bcrypt.checkpw.

### FR-04 🟠 Token Refresh

- **描述**: Allow users to obtain a new access token using a valid refresh token.

- **优先级**: HIGH

- **参与者**: ACT-01

- **依赖**: FR-02

**验收标准**:

- [ ] AC1: Valid refresh token returns 200 with new access token and optionally new refresh token (rotation).

- [ ] AC2: Expired or revoked refresh token returns 401.

- [ ] AC3: Refresh token is stored in database and can be revoked.

### FR-05 🟠 Role-Based Access Control

- **描述**: Define roles (e.g., admin, user) and assign them to users. Protect endpoints based on role.

- **优先级**: HIGH

- **参与者**: ACT-02, ACT-03

- **依赖**: FR-02

**验收标准**:

- [ ] AC1: Admin can create, read, update, delete roles.

- [ ] AC2: Admin can assign a role to a user.

- [ ] AC3: Endpoints can be decorated with required role; unauthorized roles return 403.


## 3.3 非功能需求 (NFR)

### NFR-01: Security Vulnerability Protection

- **类别**: security

- **描述**: The system must protect against common vulnerabilities: SQL injection, XSS, CSRF, and broken authentication.

- **目标指标**: Zero critical or high severity vulnerabilities in OWASP Top 10 scan.

### NFR-02: Test Coverage

- **类别**: reliability

- **描述**: The system must have comprehensive automated tests covering all functional requirements.

- **目标指标**: Code coverage >= 90% for unit and integration tests.

### NFR-03: Database Model Design

- **类别**: maintainability

- **描述**: Database models must be designed with proper relationships, indexes, and migrations using Alembic.

- **目标指标**: All tables have primary keys, foreign keys with indexes, and migration scripts are versioned.

### NFR-04: API Response Time

- **类别**: performance

- **描述**: API endpoints should respond quickly under normal load.

- **目标指标**: 95th percentile response time < 500ms for authentication endpoints.

### NFR-05: Error Messages

- **类别**: usability

- **描述**: Error responses should be clear and consistent, using standard HTTP status codes and JSON format.

- **目标指标**: All error responses include 'detail' field with human-readable message.


## 3.4 安全需求 (NFR-SEC)

| ID | 类别 | OWASP | 描述 | 目标 |
|----|------|-------|------|------|
| NFR-SEC-01 | authentication | A2: Broken Authentication | All API endpoints must require valid JWT authentication exce | 0 unauthenticated requests succeed on protected en |
| NFR-SEC-02 | data_protection | A3: Sensitive Data Exposure | All passwords hashed with bcrypt (work factor >= 12). | No plaintext passwords in storage or logs. |
| NFR-SEC-03 | input_validation | A1: Injection | Validate and sanitize all user inputs; use parameterized que | 0 SQL injection or XSS vulnerabilities. |
| NFR-SEC-04 | access_control | A5: Broken Access Control | Implement role-based access control; endpoints check user ro | 0 unauthorized role accesses succeed. |

## 3.5 可观测性需求 (NFR-OBS)

- **NFR-OBS-01** [logging]: Structured JSON logging with request_id propagation. — 目标: Every request has traceable log entries from entry to exit.

- **NFR-OBS-02** [health_check]: /health endpoint returning service + dependency status. — 目标: Response within 1 second, includes DB status.

- **NFR-OBS-03** [metrics]: Prometheus metrics: request count, latency (p50/p95/p99), error rate. — 目标: Metrics available at /metrics endpoint.


## 3.6 用例规格 (UC)

### UC-01: Register New User

- **参与者**: ACT-01

**前置条件**:

  - User is not logged in.

  - Email is not already registered.

**主流程**:

  1. 1. User submits registration form with email and password.

  2. 2. System validates email format and password strength.

  3. 3. System checks if email already exists; if so, return error.

  4. 4. System hashes password with bcrypt (work factor 12).

  5. 5. System creates new User record with role 'user' and is_active=true.

  6. 6. System returns 201 with user ID and email.

**备选流程**:

  - **当 Invalid email format**:

      - 2a. System returns 400 with validation error.

  - **当 Weak password**:

      - 2b. System returns 400 with password policy error.

  - **当 Duplicate email**:

      - 3a. System returns 409 with 'Email already registered'.

**后置条件**:

  - User account is created and stored in database.

  - Password is hashed and never stored in plaintext.

**业务规则**:

  - Password must be at least 8 characters, contain uppercase, lowercase, and digit.

  - Email must be unique.

### UC-02: User Login

- **参与者**: ACT-01

**前置条件**:

  - User has registered account.

  - User is not currently authenticated.

**主流程**:

  1. 1. User submits email and password.

  2. 2. System looks up user by email.

  3. 3. If user not found, return 401.

  4. 4. System verifies password against stored bcrypt hash.

  5. 5. If password invalid, return 401.

  6. 6. If user account is inactive, return 403.

  7. 7. System generates JWT access token (15 min expiry) and refresh token (7 days expiry).

  8. 8. System stores refresh token in database.

  9. 9. System returns 200 with tokens.

**备选流程**:

  - **当 User not found**:

      - 3a. Return 401 'Invalid credentials'.

  - **当 Password mismatch**:

      - 5a. Return 401 'Invalid credentials'.

  - **当 Inactive account**:

      - 6a. Return 403 'Account disabled'.

**后置条件**:

  - User receives valid JWT tokens.

  - Refresh token is stored in database.

**业务规则**:

  - Access token expires in 15 minutes.

  - Refresh token expires in 7 days.

### UC-03: Refresh Access Token

- **参与者**: ACT-01

**前置条件**:

  - User has a valid refresh token.

**主流程**:

  1. 1. User sends refresh token to /refresh endpoint.

  2. 2. System validates refresh token (exists, not expired, not revoked).

  3. 3. If invalid, return 401.

  4. 4. System generates new access token (15 min expiry).

  5. 5. Optionally, system rotates refresh token (issue new, revoke old).

  6. 6. System returns 200 with new access token (and optionally new refresh token).

**备选流程**:

  - **当 Token expired or revoked**:

      - 3a. Return 401 'Invalid refresh token'.

**后置条件**:

  - New access token is issued.

  - Old refresh token may be revoked if rotation is implemented.

**业务规则**:

  - Refresh token rotation is recommended for security.

### UC-04: Manage Roles (Admin)

- **参与者**: ACT-02

**前置条件**:

  - Admin is authenticated with JWT.

  - Admin has 'admin' role.

**主流程**:

  1. 1. Admin sends request to create/update/delete role.

  2. 2. System validates admin permissions.

  3. 3. System performs CRUD operation on Role table.

  4. 4. System returns appropriate response (201, 200, 204).

**备选流程**:

  - **当 Non-admin user attempts**:

      - 2a. Return 403 Forbidden.

  - **当 Role name already exists**:

      - 3a. Return 409 Conflict.

**后置条件**:

  - Role is created/updated/deleted in database.

**业务规则**:

  - Only admin can manage roles.

  - Role names must be unique.


# 风险评估

| 风险 | 概率 | 影响 | 缓解措施 | 应急方案 |
|------|------|------|---------|---------|
| Insecure password storage due to misconfiguration  | low | high | Enforce work factor >= 12 in code and configuratio | Audit and rotate all passwords if breach detected. |
| JWT token theft leading to unauthorized access. | medium | high | Use short-lived access tokens (15 min) and refresh | Implement token revocation list and force re-login |
| SQL injection via user input. | low | high | Use SQLAlchemy ORM with parameterized queries; avo | Run regular security scans and penetration tests. |
| Insufficient test coverage leading to undetected b | medium | medium | Enforce minimum 90% coverage; include unit, integr | Add automated CI pipeline to fail on coverage drop |

# 附录


## 附录 A: 需求追溯矩阵 (RTM)

*此矩阵将在设计阶段完成后自动填充。*

## 附录 B: 问题跟踪

| 问题 ID | 描述 | 状态 | 负责人 |
|---------|------|------|--------|
| — | — | — | — |
