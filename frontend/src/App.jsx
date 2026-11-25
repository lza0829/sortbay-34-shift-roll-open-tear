import React from 'react'
import { NavLink, Route, Routes } from 'react-router-dom'
import Overview from './pages/Overview'
import Scans from './pages/Scans'
import Chutes from './pages/Chutes'
import RoutesPage from './pages/RoutesPage'
import Bags from './pages/Bags'
import Diverts from './pages/Diverts'
import Shifts from './pages/Shifts'
import Settings from './pages/Settings'

const links = [
  ['/', '总览'],
  ['/扫描', '过机扫描'],
  ['/口门', '口门'],
  ['/路由表', '路由表'],
  ['/回流', '错分回流'],
  ['/封袋', '封袋'],
  ['/班次', '班次对账'],
  ['/设置', '设置'],
]

export default function App() {
  return (
    <div className="shell">
      <header className="topbar">
        <div>
          <div className="brand">分拣过机台</div>
          <div className="brand-sub">扫描装载 · 口门路由 · 错分回流 · 封袋班次</div>
        </div>
        <nav className="nav">
          {links.map(([to, label]) => (
            <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => (isActive ? 'active' : '')}>
              {label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main className="main">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/扫描" element={<Scans />} />
          <Route path="/口门" element={<Chutes />} />
          <Route path="/路由表" element={<RoutesPage />} />
          <Route path="/回流" element={<Diverts />} />
          <Route path="/封袋" element={<Bags />} />
          <Route path="/班次" element={<Shifts />} />
          <Route path="/设置" element={<Settings />} />
        </Routes>
      </main>
    </div>
  )
}
