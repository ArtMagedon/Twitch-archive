import { useCallback, useEffect, useMemo, useState } from 'react'
import { Activity, Archive, CircleDot, Download, HardDrive, Plus, Radio, RefreshCw, Trash2, Tv, X } from 'lucide-react'

async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) },
  })
  if (!response.ok) {
    let message = `Ошибка HTTP ${response.status}`
    try { message = (await response.json()).detail || message } catch { /* response is not JSON */ }
    throw new Error(message)
  }
  return response.status === 204 ? null : response.json()
}

const formatDate = value => value ? new Date(value).toLocaleString('ru-RU') : '—'
const formatSize = value => {
  if (!value) return '—'
  const units = ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ']
  let size = value, index = 0
  while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1 }
  return `${size.toFixed(index > 1 ? 1 : 0)} ${units[index]}`
}

function ChannelStatus({ channel }) {
  if (!channel.enabled) return <span className="status muted">Отключён</span>
  if (channel.recording) return <span className="status recording">● Запись</span>
  if (channel.is_live) return <span className="status live">● В эфире</span>
  return <span className="status muted">● Не в эфире</span>
}

export default function App() {
  const [channels, setChannels] = useState([])
  const [records, setRecords] = useState([])
  const [stats, setStats] = useState({})
  const [settings, setSettings] = useState({})
  const [login, setLogin] = useState('')
  const [filter, setFilter] = useState('all')
  const [tab, setTab] = useState('channels')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [channelData, recordingData, statusData, settingData] = await Promise.all([
        api('/channels'), api('/recordings'), api('/status'), api('/settings'),
      ])
      setChannels(channelData)
      setRecords(recordingData)
      setStats(statusData)
      setSettings(settingData)
      setError('')
    } catch (exception) { setError(exception.message) }
  }, [])

  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, 10000)
    return () => clearInterval(timer)
  }, [refresh])

  async function addChannel(event) {
    event.preventDefault()
    setBusy(true)
    try {
      await api('/channels', { method: 'POST', body: JSON.stringify({ login: login.trim().replace(/^@/, '') }) })
      setLogin('')
      await refresh()
    } catch (exception) { setError(exception.message) }
    finally { setBusy(false) }
  }

  async function setChannelEnabled(channel, enabled) {
    try {
      await api(`/channels/${channel.id}`, { method: 'PATCH', body: JSON.stringify({ enabled }) })
      await refresh()
    } catch (exception) { setError(exception.message) }
  }

  async function removeChannel(channel) {
    if (!window.confirm(`Удалить канал ${channel.login}? История записей будет удалена из базы, сами видеофайлы останутся на диске.`)) return
    try { await api(`/channels/${channel.id}`, { method: 'DELETE' }); await refresh() }
    catch (exception) { setError(exception.message) }
  }

  async function removeRecording(record) {
    if (!window.confirm('Удалить запись и соответствующий видеофайл?')) return
    try { await api(`/recordings/${record.id}`, { method: 'DELETE' }); await refresh() }
    catch (exception) { setError(exception.message) }
  }

  const filteredChannels = useMemo(() => channels.filter(channel => {
    if (filter === 'live') return channel.is_live
    if (filter === 'recording') return channel.recording
    if (filter === 'offline') return !channel.is_live
    return true
  }), [channels, filter])

  return <div className="layout">
    <aside>
      <div className="brand"><span><Radio /></span><div><b>Twitch Archive</b><small>STREAM MONITOR</small></div></div>
      <label className="sidebar-label">РАБОЧАЯ ОБЛАСТЬ</label>
      <button className={`nav ${tab === 'channels' ? 'active' : ''}`} onClick={() => setTab('channels')}><Tv />Каналы</button>
      <button className={`nav ${tab === 'records' ? 'active' : ''}`} onClick={() => setTab('records')}><Archive />Архив <em>{stats.recordings || 0}</em></button>
      <div className="sidebottom"><label className="sidebar-label">СИСТЕМА</label><p><i /> Мониторинг активен</p><small>Опрос: {settings.poll_interval_seconds || '—'} сек.</small><small>Проверка: Streamlink, без API</small></div>
    </aside>

    <main>
      <header><div><small>TWITCH / ARCHIVE</small><h1>{tab === 'channels' ? 'Мониторинг каналов' : 'Архив трансляций'}</h1></div><button className="icon" onClick={refresh} title="Обновить"><RefreshCw /></button></header>
      {error && <div className="error" role="alert">{error}<button onClick={() => setError('')} aria-label="Закрыть"><X /></button></div>}
      <section className="stats">
        <article><Tv /><b>{stats.channels || 0}</b><small>Каналов</small></article>
        <article><CircleDot /><b>{stats.live || 0}</b><small>В эфире</small></article>
        <article><Activity /><b>{stats.recording || 0}</b><small>Идёт запись</small></article>
        <article><Archive /><b>{stats.recordings || 0}</b><small>Всего записей</small></article>
      </section>

      {tab === 'channels' ? <>
        <section className="panel">
          <div className="heading"><div><h2>Отслеживаемые каналы</h2><p>Добавьте Twitch-логин для автоматического мониторинга.</p></div><span>{channels.length} каналов</span></div>
          <form onSubmit={addChannel}><div className="input"><b>@</b><input value={login} onChange={event => setLogin(event.target.value)} placeholder="twitch_channel" maxLength="25" pattern="[A-Za-z0-9_]+" required /></div><button className="primary" disabled={busy}><Plus />Добавить канал</button></form>
          <div className="filters">{[['all', 'Все'], ['live', 'В эфире'], ['recording', 'Записываются'], ['offline', 'Не в эфире']].map(([key, label]) => <button key={key} className={filter === key ? 'chosen' : ''} onClick={() => setFilter(key)}>{label}</button>)}</div>
          {filteredChannels.length === 0 ? <div className="empty"><Tv /><b>{channels.length ? 'Нет каналов в этой категории' : 'Каналов пока нет'}</b><small>{channels.length ? 'Измените фильтр.' : 'Добавьте канал выше.'}</small></div> : filteredChannels.map(channel => <div className="channel" key={channel.id}>
            <div className="avatar">{(channel.display_name || channel.login)[0].toUpperCase()}</div>
            <div className="channeltext"><b>{channel.display_name || channel.login} <small>@{channel.login}</small></b><p>{channel.stream_title || 'Ожидание трансляции'}{channel.game_name ? ` · ${channel.game_name}` : ''}</p>{channel.last_error && <small className="channel-error">Ошибка проверки: {channel.last_error}</small>}</div>
            <div className="channelstatus"><ChannelStatus channel={channel} /></div>
            <input className="toggle" type="checkbox" aria-label={`Мониторинг ${channel.login}`} checked={Boolean(channel.enabled)} onChange={event => setChannelEnabled(channel, event.target.checked)} />
            <button className="icon" onClick={() => removeChannel(channel)} title="Удалить канал"><Trash2 /></button>
          </div>)}
        </section>
        <div className="notice"><HardDrive /><div><b>Хранение без автоматического удаления</b><p>Архив не очищается автоматически. Следите за свободным местом на сервере.</p></div></div>
      </> : <section className="panel">
        <div className="heading"><div><h2>Записанные трансляции</h2><p>Файлы, созданные Streamlink.</p></div><span>{records.length} записей</span></div>
        {records.length === 0 ? <div className="empty"><Archive /><b>Архив пока пуст</b><small>Записи появятся после обнаружения эфиров.</small></div> : records.map(record => <div className="record" key={record.id}>
          <div className="thumb"><Tv /></div><div className="channeltext"><b>{record.title || `Трансляция ${record.login}`}</b><p>@{record.login} · {formatDate(record.started_at)}</p><small>{record.game_name || 'Без категории'} · {formatSize(record.file_size)}</small></div>
          <span className={`pill ${record.status}`}>{({ starting: 'Запуск', recording: 'Запись', completed: 'Готово', error: 'Ошибка' })[record.status] || record.status}</span>
          {record.file_exists && <a className="icon" href={record.download_url} title="Скачать запись"><Download /></a>}
          {record.status !== 'recording' && record.status !== 'starting' && <button className="icon" onClick={() => removeRecording(record)} title="Удалить запись"><Trash2 /></button>}
        </div>)}
      </section>}
      <footer>Twitch Archive 1.1.0 <span>React · FastAPI · Streamlink</span></footer>
    </main>
  </div>
}
