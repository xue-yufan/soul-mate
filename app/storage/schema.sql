
PRAGMA foreign_keys = ON;

-- ---------- meta ----------
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', '1');

-- ---------- sessions ----------
CREATE TABLE IF NOT EXISTS sessions (
  id         TEXT PRIMARY KEY,
  title      TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- ---------- messages ----------
CREATE TABLE IF NOT EXISTS messages (
  id           TEXT PRIMARY KEY,
  session_id   TEXT NOT NULL,
  role         TEXT NOT NULL CHECK(role IN ('system', 'user', 'assistant', 'tool')),
  content      TEXT NOT NULL,
  tool_call_id TEXT,
  name         TEXT,
  tool_calls   TEXT,                       -- assistant 请求的工具调用（JSON 数组）
  status       TEXT,                       -- tool 行的结果状态：ok / error / denied
  created_at   TEXT NOT NULL,
  tokens_in    INTEGER NOT NULL DEFAULT 0,
  tokens_out   INTEGER NOT NULL DEFAULT 0,
  model        TEXT,
  FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_session_created
  ON messages(session_id, created_at);

-- ---------- audit_log ----------
CREATE TABLE IF NOT EXISTS audit_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type  TEXT NOT NULL,
  actor       TEXT NOT NULL DEFAULT 'system',
  target_type TEXT,
  target_id   TEXT,
  action      TEXT NOT NULL,
  before_json TEXT,
  after_json  TEXT,
  status      TEXT NOT NULL DEFAULT 'ok',
  created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_event_created
  ON audit_log(event_type, created_at);

CREATE INDEX IF NOT EXISTS idx_audit_target
  ON audit_log(target_type, target_id);

-- ---------- cost_log ----------
CREATE TABLE IF NOT EXISTS cost_log (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id     TEXT,
  message_id     TEXT,
  provider       TEXT,
  model          TEXT,
  tokens_in      INTEGER NOT NULL DEFAULT 0,
  tokens_out     INTEGER NOT NULL DEFAULT 0,
  tts_chars      INTEGER NOT NULL DEFAULT 0,
  estimated_cost REAL NOT NULL DEFAULT 0,
  currency       TEXT NOT NULL DEFAULT 'CNY',
  created_at     TEXT NOT NULL,
  FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE SET NULL,
  FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE SET NULL
);

-- ---------- session_summaries ----------
CREATE TABLE IF NOT EXISTS session_summaries (
  session_id TEXT PRIMARY KEY,
  summary    TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS memory_facts (
  id          TEXT PRIMARY KEY,
  type        TEXT NOT NULL,
  content     TEXT NOT NULL,
  importance  REAL NOT NULL DEFAULT 0.5,
  source      TEXT,
  status      TEXT NOT NULL DEFAULT 'active',
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_facts_status_type
  ON memory_facts(status, type);

CREATE INDEX IF NOT EXISTS idx_facts_created
  ON memory_facts(created_at);

CREATE INDEX IF NOT EXISTS idx_cost_created  ON cost_log(created_at);
CREATE INDEX IF NOT EXISTS idx_cost_session  ON cost_log(session_id);

-- ---------- documents ----------
CREATE TABLE IF NOT EXISTS documents (
  id          TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  source      TEXT,                       -- 文件路径或 URL
  mime_type   TEXT,
  size_bytes  INTEGER NOT NULL DEFAULT 0,
  chunk_count INTEGER NOT NULL DEFAULT 0,
  content_hash TEXT,  
  status      TEXT NOT NULL DEFAULT 'active',  -- active / deleted
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_status
  ON documents(status, created_at);

-- 只对已完成导入的 active 文档做唯一：
-- content_hash 在导入完成时才写入，半成品与软删的历史记录都不参与
CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_content_hash
  ON documents(content_hash)
  WHERE status = 'active' AND content_hash IS NOT NULL;

-- ---------- document_chunks ----------
CREATE TABLE IF NOT EXISTS document_chunks (
  id          TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  chunk_index INTEGER NOT NULL,
  content     TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_chunks_document
  ON document_chunks(document_id, chunk_index);

-- ---------- worldbook ----------
CREATE TABLE IF NOT EXISTS worldbook (
  id          TEXT PRIMARY KEY,
  title       TEXT NOT NULL,              -- 条目名
  keywords    TEXT,                       -- 逗号分隔，用于精确命中
  content     TEXT NOT NULL,
  importance  REAL NOT NULL DEFAULT 0.7,
  status      TEXT NOT NULL DEFAULT 'active',
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_worldbook_status
  ON worldbook(status);
