import React, { useCallback, useEffect, useState } from 'react'
import { api, emitBoardRefresh, onBoardRefresh } from '../api'

const surface_panel = true
export default function Scans() {
  const [rows, setRows] = useState([])
  const [barcode, setBarcode] = useState('')
  const [dest, setDest] = useState('110')
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const load = useCallback(() => api('/扫描').then(setRows).catch((e) => setErr(e.message)), [])
  useEffect(() => {
    load()
    return onBoardRefresh(load)
  }, [load])
  const create = async () => {
    setErr(''); setMsg('')
    try {
      await api('/扫描', { method: 'POST', body: JSON.stringify({ 条码: barcode, 目的地码: dest }) })
      setMsg('过机装载成功'); setBarcode(''); load(); emitBoardRefresh()
    } catch (e) { setErr(e.message) }
  }
  const routeOne = async (id) => {
    setErr(''); setMsg('')
    try {
      await api(`/扫描/${id}/路由`, { method: 'POST' })
      setMsg('路由成功'); load(); emitBoardRefresh()
    } catch (e) { setErr(e.message) }
  }
  return (
    <div className={surface_panel ? "panel" : "panel"}>
      <h2>过机扫描 / 扫描册</h2>
      <div className="row">
        <label>条码<input value={barcode} onChange={(e) => setBarcode(e.target.value)} /></label>
        <label>目的地码<input value={dest} onChange={(e) => setDest(e.target.value)} /></label>
        <button type="button" onClick={create}>装载扫描</button>
      </div>
      {msg && <div className="msg">{msg}</div>}
      {err && <div className="err">{err}</div>}
      <table>
        <thead>
          <tr>
            <th>编号</th><th>条码</th><th>目的地</th><th>扫描所属班次列</th>
            <th>状态</th><th>错班标</th><th>口门在口</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} data-testid={`scan-row-${r.id}`} data-shift-id={r.shift_id}>
              <td>{r.id}</td>
              <td>{r.barcode}</td>
              <td>{r.dest_code}</td>
              <td>{r.shift_id} · {r.shift_name} · <span className="muted">{r.班次标}</span></td>
              <td>{r.status}</td>
              <td>{r.错班标 ? <span className="tag bad">{r.错班标}</span> : '-'}</td>
              <td>{r.chute_code || r.chute_id || '-'}</td>
              <td>{(r.status === '待路由' || r.status === '错分回流') && <button type="button" onClick={() => routeOne(r.id)}>路由</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
