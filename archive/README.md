# X-Agent 状态存档

此目录包含 X-Agent 运行时的个人状态快照，用于跨设备迁移。

## 目录结构

```
archive/
├── xagent-state/          # X-Agent 运行时状态
│   ├── config.json.template   # 配置模板（首次使用需复制为 config.json 并填入 API key）
│   ├── experience_bank.db     # 失败经验银行（SQLite）
│   └── prompt_evolution/      # Prompt 进化历史版本
└── kimi-state/            # Kimi CLI 状态（供参考）
    ├── config.template.toml   # Kimi CLI 配置模板
    └── credentials/README     # 凭证目录（不提交到 Git，需重新认证）
```

## 跨设备迁移步骤

1. 克隆本仓库
2. 复制 `archive/xagent-state/config.json.template` → `~/.xagent/config.json`
3. 在 `config.json` 中填入你的 API key
4. 复制 `archive/xagent-state/experience_bank.db` → `~/.xagent/experience_bank.db`
5. 复制 `archive/xagent-state/prompt_evolution/` → `~/.xagent/prompt_evolution/`
6. 启动 X-Agent，自适应配置会自动检测新硬件并调整

## 脱敏说明

- API key 已清空（`""`），需重新填入
- Windows 个人路径已替换为 `~/` 占位符
- Kimi OAuth 凭证未提交，需在新设备重新认证
