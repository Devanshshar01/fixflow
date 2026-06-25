# 🚀 FixFlow

> **Automatically identify the root cause of failed GitHub Actions workflows and receive actionable fixes in seconds.**

FixFlow is an open-source GitHub App that analyzes failed CI/CD workflows, pinpoints the actual reason behind the failure, and posts clear, developer-friendly explanations directly on your Pull Requests.

Instead of digging through hundreds of lines of logs, FixFlow tells you **what failed, why it failed, and how to fix it.**

---

## ✨ Why FixFlow?

Debugging CI/CD failures is one of the most frustrating parts of software development.

A single failed GitHub Actions workflow can generate hundreds or even thousands of log lines, making it difficult to identify the actual issue.

FixFlow automates that process.

It analyzes workflow logs, detects the root cause, classifies the error, and provides actionable suggestions so developers can spend less time debugging and more time building.

---

## 🔥 Features

- 🔍 Automatic GitHub Actions failure detection
- ⚡ Intelligent root-cause analysis
- 🧠 Hybrid Rule Engine + AI architecture
- 💬 Posts explanations directly on Pull Requests
- 📊 Repository failure analytics
- 📈 Error trends and build history
- 🔐 Secret & token redaction before analysis
- 🆓 Zero-cost architecture (planned)

---

## 🏗️ Architecture

```text
GitHub Actions
        │
        ▼
 GitHub Webhook
        │
        ▼
 FastAPI Backend
        │
        ▼
 Log Parser
        │
        ▼
 Rule Engine
        │
        ├──────────────► Known Issue
        │                     │
        │                     ▼
        │              Suggested Fix
        │
        ▼
 AI Analysis (Fallback)
        │
        ▼
 Root Cause
        │
        ▼
 GitHub PR Comment
```

---

## 🛠 Tech Stack

### Frontend

- Next.js 15
- React
- TypeScript
- Tailwind CSS
- shadcn/ui

### Backend

- FastAPI
- Python

### Database

- PostgreSQL
- Neon

### Integrations

- GitHub Apps
- GitHub Webhooks
- GitHub Actions API

### AI (Planned)

Initially:

- Hybrid Rule Engine

Future:

- Local domain-specific language model
- Fine-tuned CI/CD failure classifier

---

## 📁 Project Structure

```text
fixflow/
│
├── apps/
│   ├── api/
│   └── web/
│
├── packages/
│   ├── core/
│   ├── parser/
│   ├── github/
│   ├── rules/
│   ├── ai/
│   └── shared/
│
├── database/
├── docs/
└── .github/
```

---

## 🚧 Roadmap

### Phase 1

- GitHub App
- Webhook Receiver
- Log Fetching
- Rule Engine
- PR Comments

### Phase 2

- AI Root Cause Analysis
- Failure Classification
- Dashboard
- Build History

### Phase 3

- Failure Clustering
- Flaky Test Detection
- Repository Insights
- Suggested Auto Fixes

### Future Vision

- Train a lightweight CI/CD-specific model
- Fully local inference
- Plugin ecosystem
- Multi-provider CI support

---

## 🎯 Project Goals

- Make CI/CD debugging dramatically faster
- Reduce developer context switching
- Keep infrastructure completely free whenever possible
- Build an open-source alternative to expensive CI observability tools

---

## 🤝 Contributing

Contributions, feature requests, and discussions are always welcome.

If you'd like to improve FixFlow, feel free to open an issue or submit a pull request.

---

## 📜 License

MIT License

---

## ⭐ Support

If you find this project useful, consider giving it a ⭐ on GitHub.

It helps the project reach more developers.

---

Built with ❤️ to make debugging less painful.