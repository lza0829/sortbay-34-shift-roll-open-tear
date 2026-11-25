import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function RoutesPage() {
  const [rows, setRows] = useState([])
  const [chutes, setChutes] = useState([])
  const [dest, setDest] = useState('')
  const [chuteId, setChuteId] = useState('')
  const [err, setErr] = useState('')
  const load = async () => {
    setRows(await api('/路由表'))
    const cs = await api('/口门')
    setChutes(cs)
    if (!chuteId && cs[0]) setChuteId(String(cs[0].id))
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])
  const save = async () => {
    try {
      await api('/路由表', { method: 'POST', body: JSON.stringify({ 目的地码: dest, 口门编号: Number(chuteId) }) })
      setDest(''); load()
    } catch (e) { setErr(e.message) }
  }
  return (
    <div className="panel">
      <h2>路由表</h2>
      {err && <div className="err">{err}</div>}
      <div className="row">
        <label>目的地码<input value={dest} onChange={(e) => setDest(e.target.value)} /></label>
        <label>口门
          <select value={chuteId} onChange={(e) => setChuteId(e.target.value)}>
            {chutes.map((c) => <option key={c.id} value={c.id}>{c.code} {c.title}</option>)}
          </select>
        </label>
        <button type="button" onClick={save}>写入路由</button>
      </div>
      <table>
        <thead><tr><th>目的地码</th><th>口门</th><th>备注</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}><td>{r.dest_code}</td><td>{r.口门码}</td><td>{r.note}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
