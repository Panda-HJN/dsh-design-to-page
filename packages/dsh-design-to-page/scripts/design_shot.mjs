#!/usr/bin/env node
/**
 * design_shot.mjs — 零依赖截图器（Node 22+ 内置 fetch/WebSocket，直连 Chrome DevTools Protocol）
 *
 * 为什么要它：Playwright 需要下载浏览器、内网常不可用；CDP 只需要一个 Chromium 内核浏览器。
 *
 * 用法：
 *   node scripts/design_shot.mjs --url=http://localhost:3000/example/page \
 *     --out=design-ir/shot.png --width=1920 --height=1080 --dpr=2 \
 *     [--clip=x,y,w,h] [--wait=800] [--browser="C:\...\msedge.exe"] [--json=design-ir/shot.json]
 *
 * 输出：PNG + JSON（最终 URL、viewport、scrollHeight、phantomScrollbar、[data-slot] 矩形、box 采集）
 */
import { spawn } from 'node:child_process'
import { mkdirSync, writeFileSync, existsSync, rmSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { tmpdir } from 'node:os'

const MULTI = new Set(['select', 'block', 'region', 'mask'])
const args = {}
for (const raw of process.argv.slice(2)) {
  const [k, ...v] = raw.replace(/^--/, '').split('=')
  const val = v.length ? v.join('=') : true
  if (MULTI.has(k)) (args[k] ??= []).push(val)
  else args[k] = val
}

const url = args.url
if (!url) {
  console.error('missing --url')
  process.exit(2)
}
const width = Number(args.width ?? 1920)
const height = Number(args.height ?? 1080)
const dpr = Number(args.dpr ?? 2)
const out = resolve(args.out ?? 'design-ir/shot.png')
const jsonOut = args.json ? resolve(args.json) : out.replace(/\.png$/, '.json')
const waitMs = Number(args.wait ?? 900)
const port = Number(args.port ?? 9333)
const clip = args.clip ? args.clip.split(',').map(Number) : null
const browserPath = args.browser ?? process.env.DSH_BROWSER_PATH ?? ''

const CANDIDATES = [
  browserPath,
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  `${process.env.LOCALAPPDATA}\\Google\\Chrome\\Application\\chrome.exe`,
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
].filter(Boolean)

const exe = CANDIDATES.find((p) => existsSync(p))
if (!exe) {
  console.error('no chromium-based browser found; pass --browser=<exe>')
  process.exit(3)
}

const profile = resolve(tmpdir(), `dsh-shot-${process.pid}`)
const child = spawn(
  exe,
  [
    '--headless=new',
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profile}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-gpu',
    '--hide-scrollbars',
    '--force-device-scale-factor=1',
    '--font-render-hinting=none',
    'about:blank',
  ],
  { stdio: 'ignore' },
)

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function wsVersion() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/json/version`)
      if (r.ok) return await r.json()
    } catch {
      /* retry */
    }
    await sleep(150)
  }
  throw new Error('browser did not expose DevTools')
}

let ws
let seq = 0
const pending = new Map()
const events = []
let loadTimer
let loadFallback

function connect(wsUrl) {
  return new Promise((res, rej) => {
    ws = new WebSocket(wsUrl)
    ws.onopen = () => res()
    ws.onerror = (e) => rej(new Error('ws error'))
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data)
      if (msg.id && pending.has(msg.id)) {
        const { resolve: ok, reject } = pending.get(msg.id)
        pending.delete(msg.id)
        msg.error ? reject(new Error(JSON.stringify(msg.error))) : ok(msg.result)
      } else if (msg.method) {
        events.push(msg)
      }
    }
  })
}

const send = (method, params = {}) =>
  new Promise((ok, rej) => {
    const id = ++seq
    pending.set(id, { resolve: ok, reject: rej })
    ws.send(JSON.stringify({ id, method, params }))
  })

const evaluate = async (expr) => {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true })
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text + ' ' + (r.exceptionDetails.exception?.description ?? ''))
  return r.result.value
}

const main = async () => {
  await wsVersion()
  // 创建 target（新版本要求 PUT）
  const t = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' }).then((r) => r.json())
  await connect(t.webSocketDebuggerUrl)

  await send('Page.enable')
  await send('Runtime.enable')
  await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: dpr, mobile: false })

  const loaded = new Promise((res) => {
    loadTimer = setInterval(() => {
      if (events.some((e) => e.method === 'Page.loadEventFired')) {
        clearInterval(loadTimer)
        clearTimeout(loadFallback)
        res()
      }
    }, 50)
    // 兜底：页面一直不触发 load 也要继续；必须清掉，否则进程会被拖住 15s
    loadFallback = setTimeout(() => {
      clearInterval(loadTimer)
      res()
    }, 15000)
  })

  await send('Page.navigate', { url })
  await loaded
  await sleep(waitMs)
  await evaluate('document.fonts.ready.then(() => true)')
  await sleep(200)

  const metrics = await evaluate(`(() => {
    const de = document.documentElement
    const slots = [...document.querySelectorAll('[data-slot="image"]')].map(el => {
      const r = el.getBoundingClientRect()
      return { slot: el.dataset.slot, x: r.left, y: r.top, w: r.width, h: r.height }
    })
    const box = {}
    for (const sel of ${JSON.stringify([].concat(args.select ?? []).filter(Boolean))}) {
      const el = document.querySelector(sel)
      if (!el) { box[sel] = null; continue }
      const r = el.getBoundingClientRect()
      const cs = getComputedStyle(el)
      box[sel] = { rect: { x: r.left, y: r.top, w: r.width, h: r.height },
        fontSize: cs.fontSize, lineHeight: cs.lineHeight, color: cs.color, fontFamily: cs.fontFamily,
        padding: cs.padding, gap: cs.gap }
    }
    return {
      href: location.href, title: document.title,
      viewport: { w: innerWidth, h: innerHeight, dpr: devicePixelRatio },
      scrollHeight: de.scrollHeight, scrollWidth: de.scrollWidth,
      phantomScrollbar: de.scrollHeight > innerHeight,
      fonts: [...new Set([...document.fonts].map(f => f.family + ' ' + f.weight))].slice(0, 20),
      slots, box,
    }
  })()`)

  const shot = await send('Page.captureScreenshot', {
    format: 'png',
    captureBeyondViewport: false,
    ...(clip ? { clip: { x: clip[0], y: clip[1], width: clip[2], height: clip[3], scale: 1 } } : {}),
  })

  mkdirSync(dirname(out), { recursive: true })
  writeFileSync(out, Buffer.from(shot.data, 'base64'))
  const report = { url, finalUrl: metrics.href, viewport: metrics.viewport, scrollHeight: metrics.scrollHeight, phantomScrollbar: metrics.phantomScrollbar, slots: metrics.slots, box: metrics.box, fonts: metrics.fonts, browser: exe, out }
  writeFileSync(jsonOut, JSON.stringify(report, null, 2))

  const consoleErrors = events.filter((e) => e.method === 'Runtime.exceptionThrown').length
  console.log(JSON.stringify({ ...report, consoleErrors }))

  if (args['expect-path']) {
    const got = new URL(metrics.href).pathname
    if (got !== String(args['expect-path'])) {
      console.error(`expect-path mismatch: got ${got}`)
      process.exitCode = 4
    }
  }
  if (/\/login\b/.test(new URL(metrics.href).pathname)) {
    console.error('landed on /login — auth/route problem')
    process.exitCode = 5
  }
}

main()
  .catch((e) => {
    console.error(e.message)
    process.exitCode = 1
  })
  .finally(async () => {
    // 任何路径都要清掉加载兜底定时器，否则失败时进程会被拖住 15s
    clearInterval(loadTimer)
    clearTimeout(loadFallback)
    try { ws?.close() } catch {}
    try { child.kill() } catch {}
    await sleep(300)
    // 临时 profile 带 cookie/storage，必须清掉；Windows 上句柄释放有延迟，用 rmSync 自带的退避重试
    try {
      rmSync(profile, { recursive: true, force: true, maxRetries: 20, retryDelay: 150 })
    } catch {
      /* 清不掉不阻塞主流程 */
    }
  })
