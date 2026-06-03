import { Pool } from 'pg';
import sqlite3 from 'sqlite3';
import path from 'path';
import fs from 'fs';

let pgPool: Pool | null = null;
let sqliteDbPath: string | null = null;

function toCamelCase(str: string): string {
  return str.replace(/([-_][a-z])/gi, ($1) => {
    return $1.toUpperCase().replace('-', '').replace('_', '');
  });
}

const JSON_COLUMNS = new Set([
  'policy_flags', 'policyFlags',
  'story_ids', 'storyIds',
  'asset_ids', 'assetIds',
  'render_config', 'renderConfig',
  'tags',
  'request_data', 'requestData',
  'variables',
  // Channel + integration JSON blobs (never includes secrets_encrypted, which
  // the dashboard must not select).
  'config'
]);

function convertRow(row: any): any {
  if (!row || typeof row !== 'object') return row;
  const result: any = {};
  for (const key of Object.keys(row)) {
    const camelKey = toCamelCase(key);
    let val = row[key];
    
    // Auto-parse JSON strings
    if (JSON_COLUMNS.has(key) && typeof val === 'string') {
      try {
        val = JSON.parse(val);
      } catch (e) {
        // Fallback
      }
    }
    result[camelKey] = val;
  }
  return result;
}

function getSqlitePath(): string {
  if (sqliteDbPath) return sqliteDbPath;

  const configuredPath = process.env.SQLITE_PATH;
  if (configuredPath && path.isAbsolute(configuredPath)) {
    sqliteDbPath = configuredPath;
    return sqliteDbPath;
  }

  if (process.env.NODE_ENV === 'production') {
    sqliteDbPath = path.join(process.cwd(), 'data', 'storyfactory.db');
    return sqliteDbPath;
  }

  const rawPath = configuredPath || './data/storyfactory.db';
  const workerFolder = ['worker'].join('');
  const appsWorkerFolder = ['apps', workerFolder].join('/');
  const devPaths = [
    path.resolve(/* turbopackIgnore: true */ process.cwd(), appsWorkerFolder, rawPath),
    path.resolve(/* turbopackIgnore: true */ process.cwd(), `../${workerFolder}`, rawPath),
    path.resolve(/* turbopackIgnore: true */ process.cwd(), rawPath),
  ];

  const existingPath = devPaths.find((candidate) => fs.existsSync(candidate));
  sqliteDbPath = existingPath || devPaths[0];
  return sqliteDbPath;
}

export async function query<T = any>(sql: string, params: any[] = []): Promise<T[]> {
  const dbUrl = process.env.DATABASE_URL;
  if (dbUrl && (dbUrl.startsWith('postgres://') || dbUrl.startsWith('postgresql://'))) {
    if (!pgPool) {
      pgPool = new Pool({ connectionString: dbUrl });
    }
    const res = await pgPool.query(sql, params);
    return res.rows.map(convertRow);
  } else {
    const dbPath = getSqlitePath();
    const dir = path.dirname(dbPath);
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
    return new Promise((resolve, reject) => {
      const db = new sqlite3.Database(dbPath, (err) => {
        if (err) return reject(err);
      });
      
      let sqliteSql = sql;
      let sqliteParams = [...params];
      if (sql.includes('$')) {
        sqliteParams = [];
        sqliteSql = sql.replace(/\$(\d+)/g, (_match, paramIndex: string) => {
          sqliteParams.push(params[Number(paramIndex) - 1]);
          return '?';
        });
      }
      
      const isSelect = sql.trim().toLowerCase().startsWith('select');
      if (isSelect) {
        db.all(sqliteSql, sqliteParams, (err, rows) => {
          db.close();
          if (err) {
            console.error(`[DB Error] Failed to execute query "${sqliteSql}":`, err);
            resolve([]);
          } else {
            resolve((rows || []).map(convertRow) as T[]);
          }
        });
      } else {
        db.run(sqliteSql, sqliteParams, function(err) {
          db.close();
          if (err) {
            console.error(`[DB Error] Failed to run statement "${sqliteSql}":`, err);
            resolve([]);
          } else {
            resolve([{ lastID: this.lastID, changes: this.changes } as any]);
          }
        });
      }
    });
  }
}
