/**
 * dsh-design-to-page — 设计稿 → 页面 的像素回归工具集
 *
 * 四个 tool：
 *   design_ir       标注 JSON → IR（布局文法/结构语义/组件聚类/图片槽/遮罩/不变量）
 *   design_shot     用 CDP 截图（零 npm 依赖，驱动本机 Chrome/Edge）
 *   design_diff     原生 2x 像素回归（MAE / >10 / >40 / 位移 / 分区 / exit code）
 *   design_measure  裁剪 / 取色 / 平均色 / 设计-实现并排
 *
 * 实现约定：工具只做"跑脚本 + 收结果"，所有项目特异配置来自
 *   1) 目标项目根的 .design-to-page.json
 *   2) 调用参数（优先级最高）
 */
import { spawn } from 'node:child_process'
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const SCRIPTS = join(HERE, 'scripts')

export const inject = ['tools']

// ---------------------------------------------------------------- 环境探测

function readProjectConfig(dir) {
  const file = join(dir, '.design-to-page.json')
  if (!existsSync(file)) return {}
  try {
    // Windows 工具常写 BOM，JSON.parse 会直接失败
    return JSON.parse(readFileSync(file, 'utf8').replace(/^\uFEFF/, ''))
  } catch {
    return {}
  }
}

/** 从产物路径向上找项目契约文件：工具在 Host 进程里跑，cwd 不一定是项目根 */
function findProjectConfig(startDir) {
  let dir = resolve(startDir)
  for (let i = 0; i < 6; i++) {
    const cfg = readProjectConfig(dir)
    if (Object.keys(cfg).length) return cfg
    const parent = dirname(dir)
    if (parent === dir) break
    dir = parent
  }
  return {}
}

/** 用会话的工作目录，而不是 Host 进程的 cwd（会话可以 cd） */
function resolveCwd(ctx, exec) {
  try {
    const wd = ctx.get('workingDirectory')
    const dir = exec?.agent ? wd?.get?.(exec.agent) : undefined
    if (dir) return dir
  } catch {
    /* 服务不存在时退回进程 cwd */
  }
  return process.cwd()
}

/** 按路由取页面级覆盖：同一项目不同页的画板尺寸/倍率可以不同 */
function pageConfig(projectConfig, routePath) {
  const pages = projectConfig.pages ?? {}
  if (!routePath) return projectConfig
  const clean = routePath.replace(/\/$/, '') || '/'
  const hit = pages[routePath] ?? pages[clean]
  return hit ? { ...projectConfig, ...hit } : projectConfig
}

/** 在 ~/.dsh/dsh-runtimes/<runtime>/dependencies/python 下找 DSH 自带 Python */
function scanRuntimes() {
  const root = join(process.env.DSH_HOME ?? join(homedir(), '.dsh'), 'dsh-runtimes')
  const found = []
  if (!existsSync(root)) return found
  for (const dir of readdirSync(root)) {
    for (const rel of [
      ['dependencies', 'python', 'python.exe'],
      ['dependencies', 'python', 'bin', 'python3'],
      ['dependencies', 'python', 'bin', 'python'],
    ]) {
      const p = join(root, dir, ...rel)
      if (existsSync(p)) found.push(p)
    }
  }
  return found
}

function resolvePython(cfg, args, projectConfig) {
  const candidates = [
    args.pythonPath,
    cfg.pythonPath,
    process.env.DESIGN_TO_PAGE_PYTHON,
    projectConfig.pythonPath,
    ...scanRuntimes(),
  ].filter(Boolean)
  if (candidates.length) return candidates[0]
  return process.platform === 'win32' ? 'python' : 'python3'
}

function resolveBrowser(cfg, args, projectConfig) {
  const candidates = [
    args.browserPath,
    cfg.browserPath,
    process.env.DESIGN_TO_PAGE_BROWSER,
    projectConfig.browserPath,
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    join(process.env.LOCALAPPDATA ?? '', 'Google\\Chrome\\Application\\chrome.exe'),
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ].filter(Boolean)
  return candidates.find((p) => p && existsSync(p)) ?? null
}

// ---------------------------------------------------------------- 进程执行

function run(cmd, argv, { cwd, timeoutMs, env }) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(cmd, argv, {
      cwd,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', ...env },
      windowsHide: true,
    })
    let stdout = ''
    let stderr = ''
    const CAP = 256 * 1024
    const timer = setTimeout(() => {
      child.kill()
      reject(new Error(`${cmd} 超时（${timeoutMs}ms）`))
    }, timeoutMs)
    child.stdout.on('data', (d) => { if (stdout.length < CAP) stdout += d.toString('utf8') })
    child.stderr.on('data', (d) => { if (stderr.length < CAP) stderr += d.toString('utf8') })
    child.on('error', (err) => {
      clearTimeout(timer)
      reject(new Error(`无法启动 ${cmd}: ${err.message}`))
    })
    child.on('close', (code) => {
      clearTimeout(timer)
      resolvePromise({ code, stdout, stderr })
    })
  })
}

function ensureOk(result, label) {
  if (result.code !== 0) {
    const detail = (result.stderr || result.stdout || '').trim().slice(-1500)
    throw new Error(`${label} 失败（exit ${result.code}）：\n${detail}`)
  }
  return result
}

function lastJsonLine(stdout) {
  const lines = stdout.trim().split(/\r?\n/).reverse()
  for (const line of lines) {
    const t = line.trim()
    if (t.startsWith('{') && t.endsWith('}')) {
      try { return JSON.parse(t) } catch { /* keep scanning */ }
    }
  }
  return null
}

// ---------------------------------------------------------------- 工具定义

function registerDesignIr(ctx, cfg) {
  return ctx.tools.register({
    name: 'design_ir',
    description:
      '把设计平台导出的标注 JSON 解析成 IR（绝对坐标 → 布局文法 + 结构语义 + 资产线索）。' +
      '输出到 <out>/：report.md（人读）、layout.json（每容器的 grid/flex/overlay 假设与置信）、' +
      'instances.json（同构组件实例的数据数组）、invariants.json（版心/主 grid/间距/圆角）、' +
      'masks.json（图片槽+装饰矩形）、semantics.json（分割线/图标簇）、ir.json、tree.txt。' +
      '布局判定带证据与置信，置信 <0.8 的节点才需要回设计图核对。',
    parameters: {
      type: 'object',
      additionalProperties: false,
      required: ['source'],
      properties: {
        source: { type: 'string', description: '标注 JSON 的绝对路径' },
        out: { type: 'string', description: '输出目录，默认 <cwd>/design-ir' },
        boardWidth: { type: 'number', description: '画板宽（设计坐标），默认取根节点 bounds.width' },
        gutter: { type: 'number', description: '版心边距，用于装饰件判定，默认自动推断' },
        tolerance: { type: 'number', description: '行列聚类容差（设计 px），默认 2' },
        png: { type: 'string', description: '设计图 PNG：核查每个文本节点是否真的渲染，用于发现隐藏图层' },
        pngScale: { type: 'number', description: 'PNG 相对画板的倍率，缺省按 PNG 宽/画板宽' },
        decor: {
          type: 'string',
          enum: ['skip', 'background', 'alpha'],
          description:
            '装饰件策略，默认 skip（不画并遮罩）。background/alpha 表示由页面绘制、不再遮罩。禁止"不透明矩形 + 设计坐标"',
        },
      },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          out: { type: 'string' },
          nodes: { type: 'number' },
          grid: { type: 'number' },
          components: { type: 'number' },
          imageSlots: { type: 'number' },
          decorations: { type: 'number' },
          invariants: { type: 'object' },
          summary: { type: 'string' },
          hiddenLayers: { type: 'array', items: { type: 'string' } },
        files: { type: 'array', items: { type: 'string' } },
        },
      },
      render: (_args, v) => [
        {
          type: 'text',
          text:
            `IR → ${v.out}\n` +
            `节点 ${v.nodes} / grid ${v.grid} / 组件 ${v.components} / 图片槽 ${v.imageSlots} / 装饰候选 ${v.decorations}\n` +
            (v.hiddenLayers.length
              ? `隐藏图层 ${v.hiddenLayers.length} 个（PNG 上没有渲染，不要画）：${v.hiddenLayers.slice(0, 6).join('、')}\n`
              : '') +
            `\n${v.summary}\n\n文件：\n- ${v.files.join('\n- ')}`,
        },
      ],
    },
    async execute(args, exec) {
      const cwd = resolveCwd(ctx, exec)
      const out = resolve(cwd, args.out ?? 'design-ir')
      const projectConfig = findProjectConfig(dirname(resolve(cwd, args.source)))
      const python = resolvePython(cfg, args, projectConfig)
      const argv = [join(SCRIPTS, 'design_ir.py'), resolve(cwd, args.source), '--out', out]
      if (args.boardWidth) argv.push('--board', String(args.boardWidth))
      if (args.gutter !== undefined) argv.push('--gutter', String(args.gutter))
      if (args.tolerance) argv.push('--tol', String(args.tolerance))
      if (args.png) argv.push('--png', resolve(cwd, args.png))
      if (args.pngScale) argv.push('--png-scale', String(args.pngScale))
      argv.push('--decor', args.decor ?? projectConfig.decor ?? 'skip')
      const res = ensureOk(
        await run(python, argv, { cwd, timeoutMs: cfg.timeoutMs ?? 180000, env: { DSH_TOOL: 'design_ir' } }),
        'design_ir',
      )
      const files = ['report.md', 'layout.json', 'instances.json', 'invariants.json', 'masks.json', 'semantics.json', 'ir.json', 'tree.txt']
        .concat(args.png ? ['visibility.json'] : [])
        .map((f) => join(out, f))
      let invariants = {}
      try { invariants = JSON.parse(readFileSync(join(out, 'invariants.json'), 'utf8')) } catch { /* optional */ }
      const head = (() => {
        try { return readFileSync(join(out, 'report.md'), 'utf8').slice(0, 1400) } catch { return '' }
      })()
      const first = res.stdout.split(/\r?\n/).filter(Boolean)
      let hidden = []
      if (args.png) {
        try {
          hidden = JSON.parse(readFileSync(join(out, 'visibility.json'), 'utf8'))
            .filter((v) => !v.rendered)
            .map((v) => v.text)
        } catch { /* 没有就跳过 */ }
      }
      return {
        out,
        nodes: Number((first.find((l) => l.startsWith('nodes ')) ?? '').match(/\d+/)?.[0] ?? 0),
        grid: Number((first.find((l) => l.includes('grid ')) ?? '').match(/grid (\d+)/)?.[1] ?? 0),
        components: Number((first.find((l) => l.includes('组件')) ?? '').match(/组件 (\d+)/)?.[1] ?? 0),
        imageSlots: Number((first.find((l) => l.includes('图片槽')) ?? '').match(/图片槽 (\d+)/)?.[1] ?? 0),
        decorations: Number((first.find((l) => l.includes('装饰候选')) ?? '').match(/装饰候选 (\d+)/)?.[1] ?? 0),
        invariants,
        summary: head,
        hiddenLayers: hidden,
        files,
      }
    },
  })
}

function registerDesignShot(ctx, cfg) {
  return ctx.tools.register({
    name: 'design_shot',
    description:
      '用 Chrome DevTools Protocol 截取页面，视口/DPR 精确可控（零 npm 依赖）。' +
      '返回最终 URL、scrollHeight、phantomScrollbar、页面内 [data-slot] 矩形（可直接作为像素回归的遮罩）、' +
      '指定选择器的盒模型与关键 computed。落到 /login 或 --expect-path 不匹配会以非零退出。' +
      '截图尺寸建议与设计图一致（原生 2x：width=画板宽, dpr=2, height=画板高）。',
    parameters: {
      type: 'object',
      additionalProperties: false,
      required: ['url', 'out'],
      properties: {
        url: { type: 'string', description: '页面 URL（dev server 需已启动）' },
        out: { type: 'string', description: '截图输出路径' },
        width: { type: 'number', description: '视口宽（CSS px），默认 1920' },
        height: { type: 'number', description: '视口高（CSS px），默认与画板高一致，必须显式给' },
        dpr: { type: 'number', description: '设备像素比，默认 2' },
        clip: { type: 'string', description: '只截某矩形：x,y,w,h（CSS px）' },
        wait: { type: 'number', description: '加载后额外等待毫秒，默认 900' },
        expectPath: { type: 'string', description: '期望的最终路径名，例如 /example/page' },
        select: { type: 'array', items: { type: 'string' }, description: '要采集盒模型的 CSS 选择器' },
        json: { type: 'string', description: 'JSON 输出路径，默认与截图同名的 .json' },
        page: { type: 'string', description: '路由路径，用于取 .design-to-page.json 里 pages 的页面级覆盖；缺省按 url 推导' },
        storageState: {
          type: 'string',
          description:
            '登录态文件（Playwright storageState 格式，含 cookies / origins.localStorage）。仅用于需要登录的页面；不传则只适合免登录页面',
        },
        block: {
          type: 'array',
          items: { type: 'string' },
          description: '要屏蔽的资源 URL 模式（CDP setBlockedURLs），例如 "*alicdn.com*"；用于内网不可达的外部脚本',
        },
      },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          shot: { type: 'string' },
          json: { type: 'string' },
          finalUrl: { type: 'string' },
          viewport: { type: 'object' },
          scrollHeight: { type: 'number' },
          phantomScrollbar: { type: 'boolean' },
          slots: { type: 'number' },
          box: { type: 'object' },
        },
      },
      render: (_args, v) => [
        {
          type: 'text',
          text:
            `截图 ${v.shot}\n最终 URL ${v.finalUrl}\n视口 ${JSON.stringify(v.viewport)}，scrollHeight ${v.scrollHeight}\n` +
            `phantomScrollbar=${v.phantomScrollbar}，图片槽 ${v.slots} 个（已写入 JSON 供 --mask-file 使用）` +
            (v.phantomScrollbar ? '\n⚠️ 有纵向溢出：先对齐画板高度再比像素' : ''),
        },
      ],
    },
    async execute(args, exec) {
      const cwd = resolveCwd(ctx, exec)
      const target = resolve(cwd, args.out)
      const projectConfig = findProjectConfig(dirname(target))
      let routePath = args.page ?? ''
      if (!routePath) {
        try { routePath = new URL(args.url).pathname } catch { /* 非标准 URL，忽略 */ }
      }
      const pageCfg = pageConfig(projectConfig, routePath)
      const browser = resolveBrowser(cfg, args, projectConfig)
      if (!browser) throw new Error('没有找到 Chromium 内核浏览器；在 .design-to-page.json 里配置 browserPath')
      const out = resolve(cwd, args.out)
      const json = args.json ? resolve(cwd, args.json) : out.replace(/\.png$/i, '') + '.json'
      // 高度必须与画板一致；宁可直接报错，也不要静默截 1080 让后面的 diff 报尺寸不符
      const heightPx = args.height ?? pageCfg.boardHeight
      if (heightPx == null) {
        throw new Error(
          '缺少画板高度：请传 height，或在 .design-to-page.json 里配置 boardHeight（多页项目用 pages.<route>.boardHeight）',
        )
      }
      const argv = [
        join(SCRIPTS, 'design_shot.mjs'),
        `--url=${args.url}`,
        `--out=${out}`,
        `--json=${json}`,
        `--width=${args.width ?? pageCfg.boardWidth ?? 1920}`,
        `--height=${heightPx}`,
        `--dpr=${args.dpr ?? pageCfg.pngScale ?? 2}`,
        `--browser=${browser}`,
        `--port=${9400 + Math.floor(Math.random() * 500)}`,
      ]
      if (args.clip) argv.push(`--clip=${args.clip}`)
      if (args.storageState) argv.push('--storage-state', resolve(cwd, args.storageState))
      for (const b of args.block ?? []) argv.push('--block', b)
      if (args.wait) argv.push(`--wait=${args.wait}`)
      if (args.expectPath) argv.push(`--expect-path=${args.expectPath}`)
      for (const s of args.select ?? []) argv.push(`--select=${s}`)
      const res = ensureOk(
        await run(process.execPath, argv, { cwd, timeoutMs: cfg.timeoutMs ?? 180000, env: { DSH_TOOL: 'design_shot' } }),
        'design_shot',
      )
      let report = lastJsonLine(res.stdout)
      if (!report) {
        try { report = JSON.parse(readFileSync(json, 'utf8')) } catch { report = {} }
      }
      if (report.phantomScrollbar) {
        // 不失败：溢出是常见中间态，但要让模型看见
      }
      return {
        shot: out,
        json,
        finalUrl: report.finalUrl ?? args.url,
        viewport: report.viewport ?? {},
        scrollHeight: report.scrollHeight ?? 0,
        phantomScrollbar: Boolean(report.phantomScrollbar),
        slots: Array.isArray(report.slots) ? report.slots.length : 0,
        box: report.box ?? {},
      }
    },
  })
}

function registerDesignDiff(ctx, cfg) {
  return ctx.tools.register({
    name: 'design_diff',
    description:
      '在原生 2x 上做设计图与截图的像素回归：MAE、>10（含抗锯齿）、>40（结构性）、位移搜索、分区统计，' +
      '输出热力图与 metrics.json；给出 failOver/failMae 时超阈值以非零退出，可用于门禁。' +
      'mask 用设计坐标（x,y,w,h），可直接吃 design_shot 输出的 JSON（--maskFile）。' +
      '判别顺序：先看位移（dx/dy 非 0 先查版心/视口/端口）→ >40（结构）→ >10（抗锯齿）。',
    parameters: {
      type: 'object',
      additionalProperties: false,
      required: ['ref', 'shot'],
      properties: {
        ref: { type: 'string', description: '设计图 PNG（与截图同分辨率）' },
        shot: { type: 'string', description: '实现截图 PNG' },
        maskFile: { type: 'string', description: 'design_shot 输出的 JSON（自动遮 [data-slot] 图片槽）' },
        masks: { type: 'array', items: { type: 'string' }, description: '额外遮罩，设计坐标 "x,y,w,h"' },
        regions: { type: 'array', items: { type: 'string' }, description: '分区统计 "名字:x,y,w,h"' },
        out: { type: 'string', description: '输出目录（热力图与 metrics.json）' },
        designWidth: { type: 'number', description: '设计宽，默认 1920' },
        shift: { type: 'number', description: '位移搜索半径（设计 px），默认 6' },
        failOver: { type: 'number', description: '>40 百分比阈值，超过则失败（门禁用）' },
        failMae: { type: 'number', description: 'MAE 阈值，超过则失败' },
        page: { type: 'string', description: '路由路径，用于取 .design-to-page.json 里 pages 的页面级覆盖' },
        strictScale: { type: 'boolean', description: '默认 true：ref 宽/画板宽 必须是整数倍，否则报错。设为 false 才允许缩放比对' },
        noHeat: { type: 'boolean', description: '不生成热力图' },
      },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          mae: { type: 'number' },
          p10: { type: 'number' },
          p40: { type: 'number' },
          shift: { type: 'array', items: { type: 'number' } },
          regions: { type: 'array', items: { type: 'object' } },
          metrics: { type: 'string' },
          heat: { type: 'string' },
          ok: { type: 'boolean' },
        },
      },
      render: (_args, v) => [
        {
          type: 'text',
          text:
            `MAE ${v.mae} · >10 ${v.p10}% · >40 ${v.p40}%\n` +
            `位移 dx=${v.shift[0]} dy=${v.shift[1]}（PNG px）\n` +
            (v.regions.length ? `分区：${v.regions.map((r) => `${r.name} >40 ${r.p40}%`).join('，')}\n` : '') +
            `指标 ${v.metrics}${v.heat ? `\n热图 ${v.heat}` : ''}\n` +
            (v.ok ? '门禁：通过' : '门禁：未通过（超过 failOver/failMae 阈值）'),
        },
      ],
    },
    async execute(args, exec) {
      const cwd = resolveCwd(ctx, exec)
      const out = resolve(cwd, args.out ?? 'design-ir')
      const projectConfig = findProjectConfig(dirname(resolve(cwd, args.ref)))
      const pageCfg = pageConfig(projectConfig, args.page)
      const python = resolvePython(cfg, args, projectConfig)
      const argv = [
        join(SCRIPTS, 'design_diff.py'),
        '--ref', resolve(cwd, args.ref),
        '--shot', resolve(cwd, args.shot),
        '--out', out,
        '--design-width', String(args.designWidth ?? pageCfg.boardWidth ?? 1920),
        '--shift', String(args.shift ?? 6),
      ]
      if (args.maskFile) argv.push('--mask-file', resolve(cwd, args.maskFile))
      for (const m of args.masks ?? []) argv.push('--mask', m)
      for (const r of args.regions ?? []) argv.push('--region', r)
      if (args.noHeat) argv.push('--no-heat')
      if (args.failOver !== undefined) argv.push('--fail-over', String(args.failOver))
      if (args.failMae !== undefined) argv.push('--fail-mae', String(args.failMae))
      if (args.strictScale === false) argv.push('--allow-scale')
      const res = await run(python, argv, { cwd, timeoutMs: cfg.timeoutMs ?? 180000, env: { DSH_TOOL: 'design_diff' } })
      // exit 1 在设置阈值时表示"未达标"，仍要把指标带回去
      if (res.code !== 0 && res.code !== 1) ensureOk(res, 'design_diff')
      const metricsPath = join(out, 'metrics.json')
      let metrics = null
      try { metrics = JSON.parse(readFileSync(metricsPath, 'utf8')) } catch { /* fallthrough */ }
      if (!metrics) throw new Error(`design_diff 未产出指标：\n${(res.stderr || res.stdout).slice(-1200)}`)
      const heat = existsSync(join(out, 'diff-heat.png')) ? join(out, 'diff-heat.png') : ''
      return {
        mae: metrics.mae,
        p10: metrics.p10,
        p40: metrics.p40,
        shift: metrics.shift ?? [0, 0],
        regions: metrics.regions ?? [],
        metrics: metricsPath,
        heat,
        ok: res.code === 0,
      }
    },
  })
}

function registerDesignMeasure(ctx, cfg) {
  return ctx.tools.register({
    name: 'design_measure',
    description:
      '在设计图原生倍率上量测：--sample 3x3 邻域取色、--avg 区域平均色（图片占位色块用）、' +
      '--crop 局部放大（2–4x 看图标/1px）、--pair 设计 vs 实现同框并排（上设计下实现红条分隔）。' +
      '坐标是设计坐标；用裁剪图看细节，不要用裁剪图量整体定位（版心/位移交给 design_diff）。',
    parameters: {
      type: 'object',
      additionalProperties: false,
      required: ['design'],
      properties: {
        design: { type: 'string', description: '设计图 PNG' },
        shot: { type: 'string', description: '实现截图 PNG，--pair 时必需' },
        crop: { type: 'array', items: { type: 'string' }, description: '"名字:x,y,w,h[:zoom]"' },
        pair: { type: 'array', items: { type: 'string' }, description: '"名字:x,y,w,h[:zoom]"' },
        sample: { type: 'array', items: { type: 'string' }, description: '"名字:x,y"' },
        avg: { type: 'array', items: { type: 'string' }, description: '"名字@x,y,w,h"' },
        out: { type: 'string', description: '输出目录' },
        designWidth: { type: 'number', description: '设计宽，默认 1920' },
        resample: { type: 'string', enum: ['nearest', 'lanczos'], description: '放大采样：nearest 看 1px 更准（默认），lanczos 更平滑' },
      },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          scale: { type: 'number' },
          samples: { type: 'array', items: { type: 'object' } },
          avgs: { type: 'array', items: { type: 'object' } },
          files: { type: 'array', items: { type: 'object' } },
        },
      },
      render: (_args, v) => [
        {
          type: 'text',
          text:
            `scale=${v.scale}\n` +
            (v.samples.length ? `取色：${v.samples.map((s) => `${s.name} ${s.hex}`).join('，')}\n` : '') +
            (v.avgs.length ? `平均色：${v.avgs.map((s) => `${s.name} ${s.hex}`).join('，')}\n` : '') +
            (v.files.length ? `产物：\n- ${v.files.map((f) => `${f.path}（${f.size.join('×')}）`).join('\n- ')}` : ''),
        },
      ],
    },
    async execute(args, exec) {
      const cwd = resolveCwd(ctx, exec)
      const projectConfig = findProjectConfig(dirname(resolve(cwd, args.design)))
      const python = resolvePython(cfg, args, projectConfig)
      const out = resolve(cwd, args.out ?? join(projectConfig.irOut ?? 'design-ir', 'measure'))
      const argv = [
        join(SCRIPTS, 'design_measure.py'),
        resolve(cwd, args.design),
        '--out', out,
        '--design-width', String(args.designWidth ?? projectConfig.boardWidth ?? 1920),
      ]
      if (args.shot) argv.push('--shot', resolve(cwd, args.shot))
      for (const c of args.crop ?? []) argv.push('--crop', c)
      for (const p of args.pair ?? []) argv.push('--pair', p)
      for (const s of args.sample ?? []) argv.push('--sample', s)
      for (const s of args.avg ?? []) argv.push('--avg', s)
      if (args.resample) argv.push('--resample', args.resample)
      const res = ensureOk(
        await run(python, argv, { cwd, timeoutMs: cfg.timeoutMs ?? 180000, env: { DSH_TOOL: 'design_measure' } }),
        'design_measure',
      )
      const data = lastJsonLine(res.stdout)
      if (!data) throw new Error(`design_measure 未返回结果：${res.stdout.slice(-800)}`)
      return { scale: data.scale, samples: data.samples ?? [], avgs: data.avgs ?? [], files: data.files ?? [] }
    },
  })
}

export function apply(ctx, config) {
  const cfg = config ?? {}
  ctx.effect(() => registerDesignIr(ctx, cfg))
  ctx.effect(() => registerDesignShot(ctx, cfg))
  ctx.effect(() => registerDesignDiff(ctx, cfg))
  ctx.effect(() => registerDesignMeasure(ctx, cfg))
}