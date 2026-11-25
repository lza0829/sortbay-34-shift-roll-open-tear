import React, { useCallback, useEffect, useState } from 'react'
import { api, emitBoardRefresh } from '../api'

function today() {
  const d = new Date()
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

function MismatchStrip({ bar }) {
  if (!bar) return null
  const cls = bar.级别 === '错位' ? 'strip bad' : bar.级别 === '无未封班' ? 'strip warn' : 'strip'
  return (
    <div className={cls} data-testid="overview-shift-mismatch">
      总览班次错位条：{bar.文案}
      {bar.错班钉口数 > 0 && <span>（错班钉口 {bar.错班钉口数}）</span>}
    </div>
  )
}

export default function Shifts() {
  const [rows, setRows] = useState([])
  const [rec, setRec] = useState(null)
  const [arch, setArch] = useState(null)
  const [replay, setReplay] = useState(null)
  const [day, setDay] = useState(today())
  const [name, setName] = useState('下一班')
  const [receipt, setReceipt] = useState(null)
  const [blocked, setBlocked] = useState(null)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    const [shiftRows, reconciliation, architecture] = await Promise.all([
      api('/班次'),
      api('/班次/对账'),
      api('/班次/架构'),
    ])
    setRows(shiftRows)
    setRec(reconciliation)
    setArch(architecture)
  }, [])

  useEffect(() => { load().catch((e) => setErr(e.message)) }, [load])

  const openNext = async () => {
    setErr(''); setMsg(''); setReceipt(null); setBlocked(null)
    try {
      const data = await api('/班次/开下一班', {
        method: 'POST',
        body: JSON.stringify({ 营业日: day, 班次名: name }),
      })
      setReceipt(data.开班回执)
      setMsg('开班回执已生成，旧班、扫描册、口门、袋册与总览已同刷')
      await load()
      emitBoardRefresh({ type: 'shift-opened' })
    } catch (e) {
      setErr(e.message)
      if (e.data?.开班回执) {
        setBlocked(e.data)
        setReceipt(e.data.开班回执)
      }
      await load().catch(() => {})
      emitBoardRefresh({ type: 'shift-open-blocked' })
    }
  }

  const seal = async (id) => {
    setErr(''); setMsg('')
    try {
      await api(`/班次/${id}/封班`, { method: 'POST' })
      setMsg('封班成功')
      await load()
      emitBoardRefresh({ type: 'shift-sealed' })
    } catch (e) { setErr(e.message) }
  }

  const showReplay = async () => {
    try {
      setReplay(await api('/班次/开班重放'))
    } catch (e) { setErr(e.message) }
  }

  const uncleared = blocked?.未清集合 || receipt?.开班后未清集合 || receipt?.未清集合 || rec?.未清集合
  const mouths = blocked?.口门在口 || receipt?.开班后口门在口 || receipt?.口门在口 || []
  const bar = blocked?.班次错位条 || receipt?.开班后班次错位条 || rec?.班次错位条

  return (
    <div className="panel">
      <h2>班次对账 / 开下一班</h2>
      {arch && <p className="muted">全局架构：{arch.口径}（{arch.架构键}）。{arch.开班成功条件}。</p>}
      {err && <div className="err" data-testid="open-shift-error">{err}</div>}
      {msg && <div className="msg">{msg}</div>}
      {bar && <MismatchStrip bar={bar} />}

      <div className="row" data-testid="open-shift-entry">
        <label>营业日<input value={day} onChange={(e) => setDay(e.target.value)} /></label>
        <label>新班次名<input value={name} onChange={(e) => setName(e.target.value)} /></label>
        <button type="button" onClick={openNext}>开下一班</button>
        <button type="button" className="secondary" onClick={showReplay}>重放开班事件</button>
      </div>

      {rec && (
        <div className="grid">
          <div className="stat"><div className="n">{rec.扫描总数}</div><div className="l">扫描总数</div></div>
          <div className="stat"><div className="n">{rec.已路由及以上}</div><div className="l">已路由及以上</div></div>
          <div className="stat"><div className="n">{rec.已封袋数}</div><div className="l">已封袋数</div></div>
          <div className="stat"><div className="n">{rec.未清件数}</div><div className="l">未清件数</div></div>
          <div className="stat"><div className="n">{rec.集包中袋数}</div><div className="l">集包中袋</div></div>
        </div>
      )}

      {uncleared && (
        <section data-testid="uncleared-list">
          <h3>未清扫描列表（{uncleared.件数 ?? uncleared.未清件数 ?? 0}）</h3>
          <p className="muted">
            待路由 {uncleared.待路由 ?? 0} · 错分回流 {uncleared.错分回流 ?? 0} ·
            已路由未封袋 {uncleared.已路由未封袋 ?? 0} · 集包中袋 {uncleared.集包中袋数 ?? 0}
          </p>
          <div className="mini-table">
            <table>
              <thead><tr><th>编号</th><th>条码</th><th>班次</th><th>状态</th><th>错班标</th><th>口门</th></tr></thead>
              <tbody>
                {(uncleared.扫描 || []).map((s) => (
                  <tr key={s.id}>
                    <td>{s.id}</td><td>{s.barcode}</td><td>{s.shift_id} · {s.shift_name}</td>
                    <td>{s.status}</td>
                    <td>{s.错班标 ? <span className="tag bad">{s.错班标}</span> : '-'}</td>
                    <td>{s.chute_code || s.chute_id || '-'}</td>
                  </tr>
                ))}
                {!(uncleared.扫描 || []).length && <tr><td colSpan="6">无未清扫描</td></tr>}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {mouths.length > 0 && (
        <section data-testid="mouth-snapshot">
          <h3>口门在口（同刷快照）</h3>
          <table>
            <thead><tr><th>口门</th><th>在口件数</th><th>本班</th><th>错班钉口</th></tr></thead>
            <tbody>
              {mouths.map((c) => (
                <tr key={c.id}>
                  <td>{c.code}</td><td>{c.在口件数}</td><td>{c.本班在口件数}</td>
                  <td>{c.错班钉口件数 ? <span className="tag bad">{c.错班钉口件数}</span> : 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {receipt && (
        <section className="receipt" data-testid="opening-receipt">
          <h3>开班回执：{receipt.结果}</h3>
          <div>{receipt.结论}</div>
          <pre>{JSON.stringify(receipt, null, 2)}</pre>
        </section>
      )}

      {replay && (
        <section className="receipt" data-testid="opening-replay">
          <h3>开班事件重放（{replay.事件?.length || 0}）</h3>
          {replay.事件.map((e) => (
            <div key={e.id}>{e.created_at} · {e.event_type} · {e.载荷?.结论 || ''}</div>
          ))}
        </section>
      )}

      <h3>班次列</h3>
      <table>
        <thead><tr><th>编号</th><th>营业日</th><th>班次</th><th>封班</th><th>未清</th><th>集包中袋</th><th>操作</th></tr></thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.id}>
              <td>{s.id}</td><td>{s.business_day}</td><td>{s.name}</td>
              <td>{s.sealed ? '已封班' : '未封班'}</td>
              <td>{s.未清件数}</td><td>{s.集包中袋数}</td>
              <td>{!s.sealed && <button type="button" onClick={() => seal(s.id)}>封班</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
