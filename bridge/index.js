// Ponte NÃO OFICIAL entre o WhatsApp (QR Code, protocolo do WhatsApp Web via Baileys) e a plataforma.
// ATENÇÃO: isto viola os termos do WhatsApp e pode causar o banimento permanente do número conectado.
// Use apenas em um número que você aceite perder. Para uso profissional, prefira a API oficial da Meta.
//
// A ponte escuta SÓ em 127.0.0.1 e exige o segredo BRIDGE_SECRET (arquivo ../.env) em todas as chamadas.
// As credenciais da sessão do WhatsApp ficam em AUTH_DIR (por padrão FORA da pasta do projeto/OneDrive).
import http from 'node:http'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import QRCode from 'qrcode'
import pino from 'pino'
import makeWASocket, {
  DisconnectReason,
  useMultiFileAuthState,
  fetchLatestBaileysVersion,
  Browsers,
} from '@whiskeysockets/baileys'

const here = path.dirname(fileURLToPath(import.meta.url))

// --- configuração (lê ../.env sem dependências) ---
function loadEnv() {
  const env = {}
  try {
    for (const line of fs.readFileSync(path.join(here, '..', '.env'), 'utf8').split(/\r?\n/)) {
      const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/)
      if (m) env[m[1]] = m[2].replace(/^["']|["']$/g, '')
    }
  } catch {}
  return { ...env, ...process.env }
}
const env = loadEnv()
const PORT = Number(env.BRIDGE_PORT || 8788)
const SECRET = env.BRIDGE_SECRET || ''
const PLATFORM_URL = (env.BRIDGE_PLATFORM_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')
const AUTH_DIR =
  env.BRIDGE_AUTH_DIR ||
  (process.platform === 'win32'
    ? path.join(env.LOCALAPPDATA || os.homedir(), 'plataforma-bridge', 'auth')
    : path.join(os.homedir(), '.plataforma-bridge', 'auth'))
const MAX_PER_MINUTE = Number(env.BRIDGE_MAX_PER_MINUTE || 20)

if (!SECRET || SECRET.length < 16) {
  console.error('BRIDGE_SECRET ausente ou curto demais (mínimo 16 caracteres). Defina no arquivo .env.')
  process.exit(1)
}
fs.mkdirSync(AUTH_DIR, { recursive: true })
const logger = pino({ level: 'warn' })
const sleep = ms => new Promise(r => setTimeout(r, ms))
const rand = (a, b) => a + Math.random() * (b - a)

// --- chamadas para a plataforma ---
async function toPlatform(route, body, tries = 4) {
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch(PLATFORM_URL + route, {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'x-bridge-secret': SECRET },
        body: JSON.stringify(body),
      })
      if (r.ok) return true
      if (r.status < 500) {
        console.warn(`plataforma recusou ${route}: ${r.status}`)
        return false
      }
    } catch {}
    await sleep(800 * (i + 1))
  }
  console.warn(`plataforma indisponível em ${route}; evento descartado`)
  return false
}

// --- sessões ---
/** id do canal -> { sock, status, qr, phone, queue, stamps, sent } */
const sessions = new Map()

const digits = s => String(s || '').replace(/\D/g, '')
const userPart = jid => String(jid || '').split('@')[0].split(':')[0]

function textOf(m) {
  const msg = m.message || {}
  return (
    msg.conversation ||
    msg.extendedTextMessage?.text ||
    msg.imageMessage?.caption ||
    msg.videoMessage?.caption ||
    msg.ephemeralMessage?.message?.extendedTextMessage?.text ||
    msg.ephemeralMessage?.message?.conversation ||
    ''
  )
}

async function startSession(id) {
  const old = sessions.get(id)
  if (old?.sock && old.status !== 'closed') return old
  const dir = path.join(AUTH_DIR, id)
  const { state, saveCreds } = await useMultiFileAuthState(dir)
  let version
  try {
    version = (await fetchLatestBaileysVersion()).version
  } catch {}
  const s = old || { status: 'starting', qr: null, phone: null, queue: Promise.resolve(), stamps: [], sent: new Map() }
  s.status = 'starting'
  sessions.set(id, s)

  const sock = makeWASocket({
    auth: state,
    version,
    logger,
    browser: ['Plataforma de Atendimento', 'Chrome', '1.0.0'],
    markOnlineOnConnect: false, // o celular continua recebendo as notificações
    syncFullHistory: false,
  })
  s.sock = sock
  sock.ev.on('creds.update', saveCreds)

  sock.ev.on('connection.update', async ({ connection, lastDisconnect, qr }) => {
    if (qr) {
      s.status = 'qr'
      s.qr = await QRCode.toDataURL(qr, { margin: 1, width: 280 })
    }
    if (connection === 'open') {
      s.status = 'connected'
      s.qr = null
      s.phone = userPart(sock.user?.id)
      console.log(`[${id}] conectado: +${s.phone}`)
      toPlatform('/bridge/event', { channel_id: id, type: 'connected', phone: s.phone })
    }
    if (connection === 'close') {
      const code = lastDisconnect?.error?.output?.statusCode
      s.qr = null
      if (code === DisconnectReason.loggedOut || code === 401) {
        s.status = 'closed'
        console.log(`[${id}] desconectado pelo celular`)
        fs.rmSync(dir, { recursive: true, force: true })
        toPlatform('/bridge/event', { channel_id: id, type: 'logged_out' })
      } else {
        s.status = 'starting'
        const wait = code === DisconnectReason.restartRequired ? 300 : 3000 // 515 é normal logo após ler o QR
        setTimeout(() => {
          s.status = 'closed'
          startSession(id).catch(e => console.warn(`[${id}] falha ao reconectar: ${e.message}`))
        }, wait)
      }
    }
  })

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return
    for (const m of messages) {
      const jid = m.key?.remoteJid
      if (!jid || m.key.fromMe) continue
      if (jid.endsWith('@g.us') || jid === 'status@broadcast' || jid.endsWith('@newsletter')) continue // grupos e status: fora do escopo
      const alt = m.key.remoteJidAlt
      const phone = digits(jid.endsWith('@lid') ? userPart(alt) || userPart(jid) : userPart(jid))
      const text = textOf(m) || '[tipo de mensagem não suportado ainda]'
      toPlatform('/bridge/inbound', {
        channel_id: id,
        jid,
        phone,
        name: m.pushName || null,
        text,
        wamid: m.key.id,
      })
    }
  })

  sock.ev.on('messages.update', updates => {
    for (const { key, update } of updates) {
      if (!key?.fromMe || typeof update?.status !== 'number') continue
      const map = { 2: 'sent', 3: 'delivered', 4: 'read', 5: 'read' }
      if (map[update.status]) toPlatform('/bridge/status', { channel_id: id, wamid: key.id, status: map[update.status] })
    }
  })
  return s
}

// Envio com ritmo humano e limite por minuto (reduz, mas NÃO elimina, o risco de banimento).
function enqueueSend(s, jid, text, key) {
  const run = async () => {
    if (key && s.sent.has(key)) return { id: s.sent.get(key), duplicate: true }
    const now = Date.now()
    s.stamps = s.stamps.filter(t => now - t < 60_000)
    if (s.stamps.length >= MAX_PER_MINUTE) {
      const e = new Error('limite de envios por minuto atingido; tente de novo em instantes')
      e.status = 429
      throw e
    }
    s.stamps.push(now)
    await s.sock.sendPresenceUpdate('composing', jid)
    await sleep(Math.min(3500, 600 + text.length * 25) + rand(0, 600))
    const sent = await s.sock.sendMessage(jid, { text })
    await s.sock.sendPresenceUpdate('paused', jid)
    if (key) {
      s.sent.set(key, sent.key.id)
      if (s.sent.size > 2000) s.sent.delete(s.sent.keys().next().value)
    }
    await sleep(rand(400, 1200)) // intervalo mínimo entre mensagens
    return { id: sent.key.id }
  }
  const p = s.queue.then(run, run)
  s.queue = p.catch(() => {})
  return p
}

// --- API local ---
function send(res, code, body) {
  res.writeHead(code, { 'content-type': 'application/json' })
  res.end(JSON.stringify(body))
}
async function readJson(req) {
  let raw = ''
  for await (const c of req) {
    raw += c
    if (raw.length > 1_000_000) throw new Error('corpo grande demais')
  }
  return raw ? JSON.parse(raw) : {}
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://localhost')
    if (url.pathname === '/health') return send(res, 200, { ok: true, sessions: sessions.size })
    if (req.headers['x-bridge-secret'] !== SECRET) return send(res, 401, { error: 'segredo inválido' })
    const m = url.pathname.match(/^\/sessions\/([A-Za-z0-9_-]{6,64})(?:\/(start|stop|send))?$/)
    if (!m) return send(res, 404, { error: 'rota desconhecida' })
    const [, id, action] = m

    if (req.method === 'GET' && !action) {
      const s = sessions.get(id)
      return send(res, 200, s ? { status: s.status, qr: s.qr, phone: s.phone } : { status: 'none', qr: null, phone: null })
    }
    if (req.method === 'POST' && action === 'start') {
      const s = await startSession(id)
      return send(res, 200, { status: s.status })
    }
    if (req.method === 'POST' && action === 'stop') {
      const s = sessions.get(id)
      if (s?.sock) {
        try {
          await s.sock.logout()
        } catch {}
      }
      sessions.delete(id)
      fs.rmSync(path.join(AUTH_DIR, id), { recursive: true, force: true })
      return send(res, 200, { status: 'closed' })
    }
    if (req.method === 'POST' && action === 'send') {
      const s = sessions.get(id)
      if (!s || s.status !== 'connected') return send(res, 503, { error: 'WhatsApp não está conectado', retryable: true })
      const { to, text, key } = await readJson(req)
      if (!to || !text) return send(res, 422, { error: 'to e text são obrigatórios', retryable: false })
      const jid = to.includes('@') ? to : `${digits(to)}@s.whatsapp.net`
      try {
        return send(res, 200, await enqueueSend(s, jid, String(text), key))
      } catch (e) {
        return send(res, e.status || 502, { error: e.message, retryable: true })
      }
    }
    return send(res, 405, { error: 'método não permitido' })
  } catch (e) {
    console.warn('erro na API local:', e.message)
    return send(res, 500, { error: 'erro interno' })
  }
})
server.listen(PORT, '127.0.0.1', () => console.log(`Ponte WhatsApp (QR) em http://127.0.0.1:${PORT}`))

// restaura as sessões salvas (reconecta sem pedir QR de novo)
for (const id of fs.readdirSync(AUTH_DIR)) {
  if (/^[A-Za-z0-9_-]{6,64}$/.test(id) && fs.statSync(path.join(AUTH_DIR, id)).isDirectory()) {
    startSession(id).catch(e => console.warn(`[${id}] não restaurou: ${e.message}`))
  }
}
process.on('unhandledRejection', e => console.warn('erro não tratado:', e?.message || e))
