PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS equipments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  equipment_type TEXT DEFAULT '',
  location TEXT DEFAULT '',
  area TEXT DEFAULT '',
  ip TEXT DEFAULT '',
  mac TEXT DEFAULT '',
  firmware TEXT DEFAULT '',
  code TEXT DEFAULT '',
  serial TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  possui_md410 INTEGER NOT NULL DEFAULT 0,
  base_model TEXT NOT NULL DEFAULT 'md400',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_equipments_ip_unique
  ON equipments(lower(trim(ip))) WHERE trim(ip) <> '';

CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day_key TEXT NOT NULL UNIQUE,
  title TEXT DEFAULT 'RELATORIO FOTOGRAFICO - MANUTENCAO PREVENTIVA DIARIA',
  company TEXT DEFAULT 'YARA',
  general_location TEXT DEFAULT 'RIG1',
  status TEXT DEFAULT 'Aberto',
  notes TEXT DEFAULT '',
  revision INTEGER NOT NULL DEFAULT 0,
  content_hash TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS images (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day_key TEXT NOT NULL,
  equipment_id INTEGER,
  kind TEXT NOT NULL CHECK(kind IN ('before','after')),
  original_name TEXT DEFAULT '',
  file_path TEXT NOT NULL UNIQUE,
  sha256 TEXT NOT NULL,
  mime_type TEXT DEFAULT '',
  size_bytes INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(equipment_id) REFERENCES equipments(id)
);
CREATE INDEX IF NOT EXISTS idx_images_sha256 ON images(sha256);
CREATE INDEX IF NOT EXISTS idx_images_day_key ON images(day_key);

CREATE TABLE IF NOT EXISTS device_base (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ip TEXT NOT NULL UNIQUE COLLATE NOCASE,
  position INTEGER NOT NULL DEFAULT 0,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS report_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  report_id INTEGER NOT NULL,
  equipment_id INTEGER NOT NULL,
  position INTEGER NOT NULL DEFAULT 0,
  entry_key TEXT NOT NULL DEFAULT '',
  device_ip TEXT NOT NULL DEFAULT '',
  attended_type TEXT NOT NULL DEFAULT '',
  title TEXT DEFAULT '',
  snapshot_json TEXT DEFAULT '{}',
  service TEXT DEFAULT '',
  status TEXT DEFAULT 'Operacional',
  notes TEXT DEFAULT '',
  before_image_id INTEGER,
  after_image_id INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(report_id) REFERENCES reports(id) ON DELETE CASCADE,
  FOREIGN KEY(equipment_id) REFERENCES equipments(id),
  FOREIGN KEY(before_image_id) REFERENCES images(id),
  FOREIGN KEY(after_image_id) REFERENCES images(id)
);
CREATE INDEX IF NOT EXISTS idx_report_items_report ON report_items(report_id);
CREATE INDEX IF NOT EXISTS idx_report_items_device_ip ON report_items(device_ip);
CREATE UNIQUE INDEX IF NOT EXISTS idx_report_items_entry
  ON report_items(report_id,entry_key) WHERE trim(entry_key) <> '';

CREATE TABLE IF NOT EXISTS device_sync (
  id INTEGER PRIMARY KEY CHECK(id=1),
  revision INTEGER NOT NULL DEFAULT 0,
  content_hash TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT OR IGNORE INTO device_sync(id) VALUES(1);
