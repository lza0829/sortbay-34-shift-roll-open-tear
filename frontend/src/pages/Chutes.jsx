import React, { useCallback, useEffect, useState } from 'react'
import { api, emitBoardRefresh, onBoardRefresh } from '../api'

export default function Chutes() {
  const [rows, setRows] = useState([])
  const [err, setErr] = useState('')
  const load = useCallback(() => api('/口门').then(setRows).catch((e) => setErr(e.message)), [])
  useEffect(() => {
    load()
    return onBoardRefresh(load)
  }, [load])
  const toggle = async (id, 状态) => {
    try {
      await api(`/口门/${id}/开闭?状态=${encodeURIComponent(状态)}`, { method: 'POST' })
      load(); emitBoardRefresh()
    }
    catch (e) { setErr(e.message) }
  }
  return (
    <div className="panel">
      <h2>口门 / 口门在口</h2>
      {err && <div className="err">{err}</div>}
      <table>
        <thead>
          <tr>
            <th>口门码</th><th>标题</th><th>状态</th><th>软容量</th>
            <th>在口件数</th><th>本班在口</th><th>错班钉口</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.id}>
              <td>{c.code}</td><td>{c.title}</td><td>{c.status}</td><td>{c.soft_cap}</td>
              <td>{c.在口件数}</td>
              <td>{c.本班在口件数}</td>
              <td>{c.错班钉口件数 ? <span className="tag bad">{c.错班钉口件数}</span> : 0}</td>
              <td>
                <button type="button" onClick={() => toggle(c.id, '口门开')}>开</button>{' '}
                <button type="button" className="secondary" onClick={() => toggle(c.id, '口门闭')}>闭</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
