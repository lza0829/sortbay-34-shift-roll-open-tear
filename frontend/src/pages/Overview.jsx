import React, { useCallback, useEffect, useState } from 'react'
import { api, onBoardRefresh } from '../api'

export default function Overview() {
  const [data, setData] = useState(null)
  const [uncleared, setUncleared] = useState(null)
  const [err, setErr] = useState('')
  const load = useCallback(async () => {
    const [overview, pending] = await Promise.all([
      api('/总览'),
      api('/扫描/未清集合').catch(() => null),
    ])
    setData(overview)
    setUncleared(pending)
  }, [])
  useEffect(() => {
    load().catch((e) => setErr(e.message))
    return onBoardRefresh(() => load().catch(() => {}))
  }, [load])
  if (err) return <div className="panel err">{err}</div>
  if (!data) return <div className="panel">加载中…</div>
  const d = data.分布 || {}
  const bar = data.班次错位条
  const stripClass = bar?.级别 === '错位' ? 'strip bad' : bar?.级别 === '无未封班' ? 'strip warn' : 'strip'
  return (
    <div className="panel">
      <h2>总览</h2>
      <p className="muted">
        总览班次：{data.班次?.business_day} {data.班次?.name} · {data.班次?.sealed ? '已封班' : '未封班'}
        {data.当前未封班班次 && data.当前未封班班次.id !== data.班次?.id && ' · 班次选择错位'}
      </p>
      <p className="muted">架构：{data.架构?.口径}</p>
      {bar && (
        <div className={stripClass} data-testid="overview-shift-mismatch">
          总览班次错位条：{bar.文案}
          {bar.错班钉口数 > 0 && ` · 错班钉口 ${bar.错班钉口数}`}
        </div>
      )}
      <div className="grid">
        {Object.entries(d).map(([k, v]) => (
          <div className="stat" key={k}><div className="n">{v}</div><div className="l">{k}</div></div>
        ))}
      </div>

      <h3>口门在口（按物理在口统计，错班件单独标红）</h3>
      <table>
        <thead><tr><th>口门</th><th>标题</th><th>状态</th><th>在口件数</th><th>本班</th><th>错班钉口</th></tr></thead>
        <tbody>
          {(data.口门 || []).map((c) => (
            <tr key={c.id}>
              <td>{c.code}</td><td>{c.title}</td><td>{c.status}</td>
              <td>{c.在口件数}</td><td>{c.本班在口件数}</td>
              <td>{c.错班钉口件数 ? <span className="tag bad">{c.错班钉口件数}</span> : 0}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {uncleared && (
        <>
          <h3>未清扫描列表（{uncleared.件数}）</h3>
          <table>
            <thead><tr><th>编号</th><th>条码</th><th>班次</th><th>状态</th><th>错班标</th></tr></thead>
            <tbody>
              {(uncleared.扫描 || []).slice(0, 20).map((s) => (
                <tr key={s.id}>
                  <td>{s.id}</td><td>{s.barcode}</td><td>{s.shift_id} · {s.shift_name}</td>
                  <td>{s.status}</td>
                  <td>{s.错班标 ? <span className="tag bad">{s.错班标}</span> : '-'}</td>
                </tr>
              ))}
              {!uncleared.扫描?.length && <tr><td colSpan="5">无未清扫描</td></tr>}
            </tbody>
          </table>
        </>
      )}

      {data.开班回执 && (
        <div className="receipt">
          <strong>最近开班回执：{data.开班回执.结果}</strong>
          <div>{data.开班回执.结论}</div>
        </div>
      )}
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
