from __future__ import annotations
import base64
import hashlib
import json
import mimetypes
import os
import re
import sqlite3
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
ROOT = Path(__file__).resolve().parent
SITE_ROOT = ROOT
DATA_DIR = ROOT / 'data'
IMAGE_DIR = DATA_DIR / 'images'
DB_PATH = DATA_DIR / 'database.db'
SCHEMA_PATH = ROOT / 'schema.sql'
DAY_RE = re.compile('^\\d{4}-\\d{2}-\\d{2}$')
MONTH_RE = re.compile('^\\d{4}-\\d{2}$')
DATA_URL_RE = re.compile('^data:(image/[a-zA-Z0-9.+-]+);base64,(.*)$', re.DOTALL)
IMAGE_URL_RE = re.compile('^/?images/(.+)$')
EXT_BY_MIME = {'image/jpeg': '.jpg', 'image/jpg': '.jpg', 'image/png': '.png', 'image/webp': '.webp', 'image/gif': '.gif'}

def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA journal_mode = WAL')
    return conn

def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row['name']) for row in conn.execute(f'PRAGMA table_info({table})')}

def ensure_column(conn: sqlite3.Connection, table: str, name: str, definition: str) -> None:
    if name not in table_columns(conn, table):
        conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')

def migrate_db(conn: sqlite3.Connection) -> None:
    ensure_column(conn, 'report_items', 'entry_key', "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, 'report_items', 'device_ip', "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, 'report_items', 'attended_type', "TEXT NOT NULL DEFAULT ''")
    conn.execute('CREATE INDEX IF NOT EXISTS idx_report_items_device_ip ON report_items(device_ip)')
    conn.execute("\n        CREATE UNIQUE INDEX IF NOT EXISTS idx_report_items_entry\n        ON report_items(report_id, entry_key)\n        WHERE entry_key <> ''\n        ")

def init_db() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding='utf-8'))
        migrate_db(conn)
        conn.commit()

def safe_day_key(value: str) -> str:
    value = str(value or '').strip()
    if not DAY_RE.match(value):
        raise ValueError('dayKey invalido')
    return value

def safe_month_key(value: str) -> str:
    value = str(value or '').strip()
    if not MONTH_RE.match(value):
        raise ValueError('month invalido; use AAAA-MM')
    return value

def safe_text(value) -> str:
    return str(value or '').strip()

def normalize_ip(value) -> str:
    return safe_text(value).lower()

def br_to_iso(value: str) -> str:
    parts = safe_text(value).split('/')
    if len(parts) == 3:
        return safe_day_key(f'{parts[2]}-{parts[1]}-{parts[0]}')
    return safe_day_key(value)

def json_response(handler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Cache-Control', 'no-store')
    handler.end_headers()
    handler.wfile.write(body)

def read_json_body(handler) -> dict:
    length = int(handler.headers.get('Content-Length') or 0)
    raw = handler.rfile.read(length)
    return json.loads(raw.decode('utf-8') or '{}') if raw else {}

def read_multipart_file(handler) -> bytes | None:
    content_type = handler.headers.get('Content-Type', '')
    match = re.search('boundary=([^;]+)', content_type)
    if not match:
        return None
    boundary = ('--' + match.group(1).strip().strip('"')).encode('utf-8')
    length = int(handler.headers.get('Content-Length') or 0)
    body = handler.rfile.read(length)
    for part in body.split(boundary):
        if b'name="file"' not in part or b'\r\n\r\n' not in part:
            continue
        _, content = part.split(b'\r\n\r\n', 1)
        return content.rstrip(b'\r\n-')
    return None

def equipment_payload(item: dict) -> dict:
    attended_type = safe_text(item.get('attendedType') or item.get('equipmentType'))
    base_type = safe_text(item.get('baseEquipmentType'))
    if not base_type or base_type.upper() == 'MD410':
        base_type = 'MD400' if attended_type.upper() == 'MD410' else attended_type
    return {'name': safe_text(item.get('location') or item.get('title') or 'Equipamento'), 'equipment_type': base_type, 'location': safe_text(item.get('location')), 'area': safe_text(item.get('area')), 'ip': safe_text(item.get('ip')), 'mac': safe_text(item.get('mac')), 'firmware': safe_text(item.get('fw')), 'code': safe_text(item.get('codigo')), 'serial': safe_text(item.get('serial')), 'notes': safe_text(item.get('notes'))}

def upsert_equipment(conn: sqlite3.Connection, item: dict) -> int:
    payload = equipment_payload(item)
    ip = normalize_ip(payload['ip'])
    existing = None
    if ip:
        existing = conn.execute('SELECT id FROM equipments WHERE lower(trim(ip)) = ? ORDER BY id LIMIT 1', (ip,)).fetchone()
    if not existing:
        existing = conn.execute('\n            SELECT id FROM equipments\n            WHERE name = ? AND location = ? AND ip = ? AND serial = ?\n            ORDER BY id LIMIT 1\n            ', (payload['name'], payload['location'], payload['ip'], payload['serial'])).fetchone()
    if existing:
        conn.execute("\n            UPDATE equipments\n            SET name = COALESCE(NULLIF(?, ''), name),\n                equipment_type = COALESCE(NULLIF(?, ''), equipment_type),\n                location = COALESCE(NULLIF(?, ''), location),\n                area = COALESCE(NULLIF(?, ''), area),\n                ip = COALESCE(NULLIF(?, ''), ip),\n                mac = COALESCE(NULLIF(?, ''), mac),\n                firmware = COALESCE(NULLIF(?, ''), firmware),\n                code = COALESCE(NULLIF(?, ''), code),\n                serial = COALESCE(NULLIF(?, ''), serial),\n                notes = COALESCE(NULLIF(?, ''), notes),\n                updated_at = CURRENT_TIMESTAMP\n            WHERE id = ?\n            ", (*payload.values(), existing['id']))
        return int(existing['id'])
    cur = conn.execute('\n        INSERT INTO equipments\n        (name, equipment_type, location, area, ip, mac, firmware, code, serial, notes)\n        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n        ', tuple(payload.values()))
    return int(cur.lastrowid)

def upsert_report(conn: sqlite3.Connection, report: dict) -> int:
    day_key = safe_day_key(report.get('dayKey') or br_to_iso(report.get('header', {}).get('dataRelatorio')))
    header = report.get('header') or {}
    title = safe_text(header.get('title')) or 'RELATÓRIO FOTOGRÁFICO — MANUTENÇÃO PREVENTIVA DIÁRIA'
    meta = header.get('metaValues') or []
    company = safe_text(header.get('empresa') or (meta[0] if len(meta) > 0 else '')) or 'YARA'
    general_location = safe_text(header.get('localGeral') or (meta[1] if len(meta) > 1 else '')) or 'RIG1'
    summary = header.get('summary') or []
    status = safe_text(summary[2] if len(summary) > 2 else 'Concluído')
    notes = safe_text(header.get('footer'))
    existing = conn.execute('SELECT id FROM reports WHERE day_key = ?', (day_key,)).fetchone()
    if existing:
        conn.execute('\n            UPDATE reports\n            SET title = ?, company = ?, general_location = ?, status = ?, notes = ?, updated_at = CURRENT_TIMESTAMP\n            WHERE id = ?\n            ', (title, company, general_location, status, notes, existing['id']))
        return int(existing['id'])
    cur = conn.execute('\n        INSERT INTO reports (day_key, title, company, general_location, status, notes)\n        VALUES (?, ?, ?, ?, ?, ?)\n        ', (day_key, title, company, general_location, status, notes))
    return int(cur.lastrowid)

def image_bytes_from_value(value: str) -> tuple[bytes, str, str] | None:
    value = safe_text(value)
    match = DATA_URL_RE.match(value)
    if match:
        mime_type, encoded = match.groups()
        return (base64.b64decode(encoded, validate=False), mime_type, '')
    match = IMAGE_URL_RE.match(value)
    if match:
        rel = unquote(match.group(1)).replace('/', os.sep)
        path = (IMAGE_DIR / rel).resolve()
        if IMAGE_DIR.resolve() not in path.parents or not path.exists():
            return None
        return (path.read_bytes(), mimetypes.guess_type(path.name)[0] or 'image/jpeg', path.name)
    return None

def store_image_bytes(conn: sqlite3.Connection, day_key: str, equipment_id: int | None, kind: str, content: bytes, mime_type: str, original_name: str='') -> tuple[int, bool]:
    digest = hashlib.sha256(content).hexdigest()
    existing = conn.execute('SELECT id FROM images WHERE sha256 = ?', (digest,)).fetchone()
    if existing:
        return (int(existing['id']), True)
    day_dir = IMAGE_DIR / day_key
    day_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(original_name).suffix.lower() if original_name and Path(original_name).suffix else EXT_BY_MIME.get(mime_type.lower(), '.jpg')
    filename = f"equipment-{equipment_id or 'unknown'}-{kind}-{digest[:12]}{ext}"
    target = day_dir / filename
    target.write_bytes(content)
    rel_path = target.relative_to(DATA_DIR).as_posix()
    cur = conn.execute('\n        INSERT INTO images (day_key, equipment_id, kind, original_name, file_path, sha256, mime_type, size_bytes)\n        VALUES (?, ?, ?, ?, ?, ?, ?, ?)\n        ', (day_key, equipment_id, kind, original_name, rel_path, digest, mime_type, len(content)))
    return (int(cur.lastrowid), False)

def update_item_image(conn: sqlite3.Connection, day_key: str, item: dict, item_id: int, equipment_id: int, field: str, kind: str, column: str) -> tuple[str | None, str | None]:
    value = safe_text(item.get(field))
    removed = bool(item.get(f'{field}Removed'))
    if removed:
        conn.execute(f'UPDATE report_items SET {column} = NULL WHERE id = ?', (item_id,))
        return (None, None)
    if not value:
        return (None, None)
    image_data = image_bytes_from_value(value)
    if image_data is None:
        return (None, f'{field} nao foi reconhecida')
    image_id, reused = store_image_bytes(conn, day_key, equipment_id, kind, image_data[0], image_data[1], image_data[2] or f'{field}.jpg')
    conn.execute(f'UPDATE report_items SET {column} = ? WHERE id = ?', (image_id, item_id))
    if reused:
        return (None, None)
    row = conn.execute('SELECT file_path FROM images WHERE id = ?', (image_id,)).fetchone()
    return (str(row['file_path']), None)

def save_legacy_report(report: dict) -> dict:
    day_key = safe_day_key(report.get('dayKey') or br_to_iso(report.get('header', {}).get('dataRelatorio')))
    report['dayKey'] = day_key
    saved_images: list[str] = []
    warnings: list[str] = []
    with connect() as conn:
        report_id = upsert_report(conn, report)
        existing_rows = conn.execute('SELECT * FROM report_items WHERE report_id = ? ORDER BY position, id', (report_id,)).fetchall()
        by_entry = {safe_text(row['entry_key']): row for row in existing_rows if safe_text(row['entry_key'])}
        by_position = {int(row['position']): row for row in existing_rows}
        kept_ids: set[int] = set()
        for position, raw_item in enumerate(report.get('equipment') or [], start=1):
            if not isinstance(raw_item, dict):
                continue
            item = dict(raw_item)
            entry_key = safe_text(item.get('entryId')) or f'legacy-{position}'
            attended_type = safe_text(item.get('attendedType') or item.get('equipmentType')) or 'MD400'
            equipment_id = upsert_equipment(conn, item)
            device_ip = safe_text(item.get('ip'))
            snapshot = dict(item)
            snapshot['beforeImage'] = ''
            snapshot['afterImage'] = ''
            snapshot.pop('beforeImageRemoved', None)
            snapshot.pop('afterImageRemoved', None)
            existing = by_entry.get(entry_key) or by_position.get(position)
            if existing and int(existing['id']) not in kept_ids:
                item_id = int(existing['id'])
                conn.execute('\n                    UPDATE report_items\n                    SET equipment_id = ?, position = ?, entry_key = ?, device_ip = ?, attended_type = ?,\n                        title = ?, snapshot_json = ?, service = ?, status = ?, notes = ?,\n                        updated_at = CURRENT_TIMESTAMP\n                    WHERE id = ?\n                    ', (equipment_id, position, entry_key, device_ip, attended_type, safe_text(item.get('title')) or f'Equipamento {position:02d}', json.dumps(snapshot, ensure_ascii=False), safe_text(item.get('service')), safe_text(item.get('status')) or 'Operacional', safe_text(item.get('notes')), item_id))
            else:
                cur = conn.execute('\n                    INSERT INTO report_items\n                    (report_id, equipment_id, position, entry_key, device_ip, attended_type,\n                     title, snapshot_json, service, status, notes)\n                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n                    ', (report_id, equipment_id, position, entry_key, device_ip, attended_type, safe_text(item.get('title')) or f'Equipamento {position:02d}', json.dumps(snapshot, ensure_ascii=False), safe_text(item.get('service')), safe_text(item.get('status')) or 'Operacional', safe_text(item.get('notes'))))
                item_id = int(cur.lastrowid)
            kept_ids.add(item_id)
            for field, kind, column in (('beforeImage', 'before', 'before_image_id'), ('afterImage', 'after', 'after_image_id')):
                saved_path, warning = update_item_image(conn, day_key, item, item_id, equipment_id, field, kind, column)
                if saved_path:
                    saved_images.append(saved_path)
                if warning:
                    warnings.append(f'item {position}: {warning}')
        for row in existing_rows:
            if int(row['id']) not in kept_ids:
                conn.execute('DELETE FROM report_items WHERE id = ?', (row['id'],))
        conn.commit()
    return {'ok': True, 'dayKey': day_key, 'savedImages': saved_images, 'warnings': warnings}

def image_url(file_path: str | None) -> str:
    if not file_path:
        return ''
    return '/images/' + str(file_path).removeprefix('images/')

def load_legacy_report(day_key: str) -> dict | None:
    day_key = safe_day_key(day_key)
    with connect() as conn:
        report_row = conn.execute('SELECT * FROM reports WHERE day_key = ?', (day_key,)).fetchone()
        if not report_row:
            return None
        item_rows = conn.execute('\n            SELECT ri.*, e.ip AS equipment_ip,\n                   bi.file_path AS before_path, ai.file_path AS after_path\n            FROM report_items ri\n            LEFT JOIN equipments e ON e.id = ri.equipment_id\n            LEFT JOIN images bi ON bi.id = ri.before_image_id\n            LEFT JOIN images ai ON ai.id = ri.after_image_id\n            WHERE ri.report_id = ?\n            ORDER BY ri.position, ri.id\n            ', (report_row['id'],)).fetchall()
    equipment: list[dict] = []
    for row in item_rows:
        try:
            item = json.loads(row['snapshot_json'] or '{}')
        except json.JSONDecodeError:
            item = {}
        item['entryId'] = safe_text(row['entry_key']) or f"legacy-{row['id']}"
        item['title'] = item.get('title') or row['title']
        item['service'] = item.get('service') or row['service'] or ''
        item['status'] = item.get('status') or row['status'] or ''
        item['notes'] = item.get('notes') or row['notes'] or ''
        item['ip'] = item.get('ip') or row['device_ip'] or row['equipment_ip'] or ''
        item['attendedType'] = row['attended_type'] or item.get('equipmentType') or ''
        item['equipmentType'] = item.get('equipmentType') or item['attendedType']
        item['beforeImage'] = image_url(row['before_path'])
        item['afterImage'] = image_url(row['after_path'])
        equipment.append(item)
    date_br = '/'.join(reversed(day_key.split('-')))
    unique_ips = {normalize_ip(item.get('ip')) for item in equipment if normalize_ip(item.get('ip'))}
    header = {'title': report_row['title'], 'badge': 'DIÁRIO', 'empresa': report_row['company'], 'localGeral': report_row['general_location'], 'dataRelatorio': date_br, 'metaValues': [report_row['company'], report_row['general_location'], 'Manutenção preventiva diária', 'Fotográfico técnico', date_br], 'summary': ['Registro fotográfico diário das atividades de manutenção preventiva realizadas nos equipamentos listados.', str(len(equipment)), report_row['status']], 'uniqueIpCount': len(unique_ips), 'totalOccurrences': len(equipment), 'footer': report_row['notes'] or ''}
    return {'dayKey': day_key, 'updatedAt': report_row['updated_at'], 'header': header, 'equipment': equipment}

def load_all_legacy_reports() -> list[dict]:
    with connect() as conn:
        days = [row['day_key'] for row in conn.execute('SELECT day_key FROM reports ORDER BY day_key')]
    return [report for day in days if (report := load_legacy_report(day))]

def count_images() -> int:
    with connect() as conn:
        return int(conn.execute('SELECT COUNT(*) AS c FROM images').fetchone()['c'])

def import_report_object(report: dict, old_data_dir: Path | None=None) -> dict:
    day_key = safe_day_key(report.get('dayKey') or br_to_iso(report.get('header', {}).get('dataRelatorio')))
    warnings: list[str] = []
    if old_data_dir:
        legacy_dir = old_data_dir / 'images' / day_key
        for index, item in enumerate(report.get('equipment') or [], start=1):
            for field in ('beforeImage', 'afterImage'):
                if item.get(field):
                    continue
                matches = list(legacy_dir.glob(f'equipamento-{index:02d}-{field}.*')) if legacy_dir.exists() else []
                if matches:
                    mime_type = mimetypes.guess_type(matches[0].name)[0] or 'image/jpeg'
                    item[field] = f'data:{mime_type};base64,' + base64.b64encode(matches[0].read_bytes()).decode('ascii')
                else:
                    warnings.append(f'item {index}: {field} ausente')
    before = count_images()
    result = save_legacy_report(report)
    after = count_images()
    return {'day_key': result['dayKey'], 'items': len(report.get('equipment') or []), 'images': after - before, 'warnings': warnings + result.get('warnings', [])}

def normalize_device(device: dict, position: int) -> dict | None:
    if not isinstance(device, dict):
        return None
    ip = safe_text(device.get('ip'))
    if not ip:
        return None
    copy = dict(device)
    copy['ip'] = ip
    copy['number'] = copy.get('number') or position
    return copy

def load_devices() -> list[dict]:
    with connect() as conn:
        rows = conn.execute('SELECT payload_json FROM device_base ORDER BY position, id').fetchall()
    devices: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        try:
            device = json.loads(row['payload_json'] or '{}')
        except json.JSONDecodeError:
            continue
        ip = normalize_ip(device.get('ip'))
        if not ip or ip in seen:
            continue
        seen.add(ip)
        devices.append(device)
    return devices

def save_devices(devices: list[dict]) -> dict:
    normalized: list[dict] = []
    index_by_ip: dict[str, int] = {}
    ignored_without_ip = 0
    duplicate_ips = 0
    for position, raw in enumerate(devices, start=1):
        device = normalize_device(raw, position)
        if device is None:
            ignored_without_ip += 1
            continue
        key = normalize_ip(device['ip'])
        if key in index_by_ip:
            duplicate_ips += 1
            target = normalized[index_by_ip[key]]
            for field, value in device.items():
                if not safe_text(target.get(field)) and safe_text(value):
                    target[field] = value
            continue
        index_by_ip[key] = len(normalized)
        normalized.append(device)
    with connect() as conn:
        conn.execute('DELETE FROM device_base')
        for position, device in enumerate(normalized, start=1):
            conn.execute('\n                INSERT INTO device_base (ip, position, payload_json)\n                VALUES (?, ?, ?)\n                ', (safe_text(device['ip']), position, json.dumps(device, ensure_ascii=False)))
        conn.commit()
    return {'ok': True, 'count': len(normalized), 'ignoredWithoutIp': ignored_without_ip, 'duplicateIpsMerged': duplicate_ips}

def monthly_coverage(month_key: str, company: str='') -> dict:
    month_key = safe_month_key(month_key)
    params: list[str] = [month_key]
    company_sql = ''
    if safe_text(company):
        company_sql = ' AND lower(trim(r.company)) = lower(trim(?))'
        params.append(safe_text(company))
    with connect() as conn:
        base_devices = load_devices()
        base_by_ip = {normalize_ip(d.get('ip')): d for d in base_devices if normalize_ip(d.get('ip'))}
        rows = conn.execute(f"\n            SELECT r.day_key,\n                   COALESCE(NULLIF(trim(ri.device_ip), ''), trim(e.ip)) AS ip,\n                   ri.attended_type\n            FROM report_items ri\n            JOIN reports r ON r.id = ri.report_id\n            LEFT JOIN equipments e ON e.id = ri.equipment_id\n            WHERE substr(r.day_key, 1, 7) = ? {company_sql}\n            ORDER BY r.day_key, ri.position, ri.id\n            ", params).fetchall()
    attended_ips = {normalize_ip(row['ip']) for row in rows if normalize_ip(row['ip'])}
    known_attended = attended_ips.intersection(base_by_ip)
    pending_ips = set(base_by_ip).difference(known_attended)
    attended = [base_by_ip[ip] for ip in sorted(known_attended)]
    pending = [base_by_ip[ip] for ip in sorted(pending_ips)]
    return {'month': month_key, 'company': safe_text(company), 'totalRegistered': len(base_by_ip), 'uniqueAttended': len(known_attended), 'pendingCount': len(pending), 'totalOccurrences': len(rows), 'md410Occurrences': sum((1 for row in rows if safe_text(row['attended_type']).upper() == 'MD410')), 'attended': attended, 'pending': pending, 'unknownIps': sorted(attended_ips.difference(base_by_ip))}

def delete_report(day_key: str) -> None:
    with connect() as conn:
        conn.execute('DELETE FROM reports WHERE day_key = ?', (safe_day_key(day_key),))
        conn.commit()

class SiteHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(SITE_ROOT), **kwargs)

    def serve_index_with_logic_patch(self) -> None:
        index_path = SITE_ROOT / 'index.html'
        if not index_path.exists():
            self.send_error(404)
            return
        text = index_path.read_text(encoding='utf-8-sig')
        tag = '<script src="/logic-fixes.js?v=2"></script>'
        if tag not in text:
            text = text.replace('</body>', f'{tag}\n</body>')
        data = text.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path in ('/', '/index.html'):
                return self.serve_index_with_logic_patch()
            if path == '/api/report':
                day_key = safe_day_key(parse_qs(parsed.query).get('dayKey', [''])[0])
                report = load_legacy_report(day_key)
                if not report:
                    return json_response(self, 404, {'ok': False, 'error': 'Relatorio nao encontrado'})
                return json_response(self, 200, {'ok': True, 'report': report})
            if path == '/api/reports':
                return json_response(self, 200, {'ok': True, 'reports': load_all_legacy_reports()})
            if path == '/api/devices':
                return json_response(self, 200, {'ok': True, 'devices': load_devices()})
            if path == '/api/coverage':
                query = parse_qs(parsed.query)
                coverage = monthly_coverage(query.get('month', [''])[0], query.get('company', [''])[0])
                return json_response(self, 200, {'ok': True, **coverage})
            if path.startswith('/images/'):
                rel = path.removeprefix('/images/')
                target = (IMAGE_DIR / rel).resolve()
                if IMAGE_DIR.resolve() not in target.parents:
                    return self.send_error(403)
                return self.serve_file(target)
        except Exception as error:
            return json_response(self, 400, {'ok': False, 'error': str(error)})
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path == '/api/report':
                return json_response(self, 200, save_legacy_report(read_json_body(self)))
            if path == '/api/devices':
                payload = read_json_body(self)
                devices = payload.get('devices') if isinstance(payload, dict) else None
                if not isinstance(devices, list):
                    return json_response(self, 400, {'ok': False, 'error': 'devices invalido'})
                return json_response(self, 200, save_devices(devices))
            if path == '/api/import-json':
                file_bytes = read_multipart_file(self)
                if not file_bytes:
                    return json_response(self, 400, {'ok': False, 'error': 'arquivo ausente'})
                payload = json.loads(file_bytes.decode('utf-8'))
                if isinstance(payload, dict) and isinstance(payload.get('reports'), list):
                    device_result = save_devices(payload.get('devices') or []) if isinstance(payload.get('devices'), list) else None
                    imported = [import_report_object(report, ROOT / 'data') for report in payload['reports'] if isinstance(report, dict)]
                    return json_response(self, 200, {'ok': True, 'devices': device_result, 'reports': imported})
                return json_response(self, 200, {'ok': True, **import_report_object(payload, ROOT / 'data')})
        except Exception as error:
            return json_response(self, 400, {'ok': False, 'error': str(error)})
        return json_response(self, 404, {'ok': False, 'error': 'Rota nao encontrada'})

    def do_DELETE(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == '/api/report':
                day_key = safe_day_key(parse_qs(parsed.query).get('dayKey', [''])[0])
                delete_report(day_key)
                return json_response(self, 200, {'ok': True, 'dayKey': day_key})
        except Exception as error:
            return json_response(self, 400, {'ok': False, 'error': str(error)})
        return json_response(self, 404, {'ok': False, 'error': 'Rota nao encontrada'})

    def serve_file(self, path: Path):
        if not path.exists() or not path.is_file():
            return self.send_error(404)
        data = path.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'public, max-age=31536000, immutable')
        self.end_headers()
        self.wfile.write(data)

def main() -> None:
    init_db()
    port = int(os.environ.get('PORT', '8880'))
    host = os.environ.get('HOST', '127.0.0.1')
    server = ThreadingHTTPServer((host, port), SiteHandler)
    print(f'YARA report system em http://{host}:{port}', flush=True)
    server.serve_forever()
if __name__ == '__main__':
    main()
