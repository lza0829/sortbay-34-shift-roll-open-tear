import React, { useEffect, useState } from 'react'
import { api } from '../api'

const surface_panel = true
export default function Settings() {
  const [rows, setRows] = useState([])
  const [key, setKey] = useState('回流自动再路由')
  const [val, setVal] = useState('否')
  const [err, setErr] = useState('')
  const load = () => api('/设置').then(setRows).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  const save = async () => {
    try {
      await api('/设置', { method: 'POST', body: JSON.stringify({ 键: key, 值: val }) })
      load()
    } catch (e) { setErr(e.message) }
  }
  return (
    <div className={surface_panel ? "panel" : "panel"}>
      <h2>设置</h2>
      {err && <div className="err">{err}</div>}
      <div className="row">
        <label>键<input value={key} onChange={(e) => setKey(e.target.value)} /></label>
        <label>值<input value={val} onChange={(e) => setVal(e.target.value)} /></label>
        <button type="button" onClick={save}>保存</button>
      </div>
      <table>
        <thead><tr><th>键</th><th>值</th></tr></thead>
        <tbody>{rows.map((r) => <tr key={r.键}><td>{r.键}</td><td>{r.值}</td></tr>)}</tbody>
      </table>
    </div>
  )
}
