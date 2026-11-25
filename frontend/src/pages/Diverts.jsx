import React, { useCallback, useEffect, useState } from 'react'
import { api, emitBoardRefresh, onBoardRefresh } from '../api'

const surface_panel = true
export default function Diverts() {
  const [batches, setBatches] = useState([])
  const [scans, setScans] = useState([])
  const [scanId, setScanId] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const load = useCallback(async () => {
    const [nextBatches, s] = await Promise.all([api('/回流批次'), api('/扫描')])
    setBatches(nextBatches)
    setScans(s.filter((x) => ['已路由', '已入袋', '集包中'].includes(x.status)))
  }, [])
  useEffect(() => {
    load().catch((e) => setErr(e.message))
    return onBoardRefresh(load)
  }, [load])
  const create = async () => {
    try {
      const result = await api('/回流批次', { method: 'POST', body: JSON.stringify({ 扫描编号: Number(scanId) }) })
      setMsg(`已建批次 ${result.批次.code}`)
      setScanId('')
      await load(); emitBoardRefresh()
    } catch (e) { setErr(e.message) }
  }
  return (
    <div className={surface_panel ? "panel" : "panel"}>
      <h2>错分回流 / 回流批次</h2>
      {msg && <div className="msg">{msg}</div>}
      {err && <div className="err">{err}</div>}
      <div className="row">
        <label>扫描
          <select value={scanId} onChange={(e) => setScanId(e.target.value)}>
            <option value="">选择</option>
            {scans.map((s) => <option key={s.id} value={s.id}>{s.id} · {s.barcode} · {s.status}</option>)}
          </select>
        </label>
        <button type="button" onClick={create}>建回流批次</button>
      </div>
      <table>
        <thead><tr><th>编号</th><th>批次码</th><th>班次</th><th>错班标</th><th>状态</th><th>未收口</th><th>时间</th></tr></thead>
        <tbody>
          {batches.map((b) => (
            <tr key={b.id} data-testid={`divert-batch-row-${b.id}`} data-shift-id={b.shift_id}>
              <td>{b.id}</td><td>{b.code}</td><td>{b.shift_id} · {b.班次名}</td>
              <td>{b.错班标 ? <span className="tag bad">{b.错班标}</span> : '-'}</td>
              <td>{b.status}</td><td>{b.未收口扫描数 ?? '-'}</td><td>{b.created_at}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
