import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Activity, Archive, AlertTriangle, CheckCircle2, CircleDot, Clipboard,
  Download, HardDrive, Plus, Radio, RefreshCw, Settings, Terminal,
  Trash2, Tv, X, ChevronDown, Wifi, FileText, Database,
} from 'lucide-react'

class ApiError extends Error {
  constructor(message, status, detail) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: {
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(options.headers || {}),
    },
  })
  if (!response.ok) {
    let detail = null
    let message = `Ошибка HTTP ${response.status}`
    try {
      detail = await response.json()
      message = detail.detail || detail.error || message
    } catch { /* response is not JSON */ }
    throw new ApiError(message, response.status, detail)
  }
  return response.status === 204 ? null : response.json()
}

const formatDate = value => value ? new Date(value).toLocaleString('ru-RU') : '—'
const formatTime = value => value ? new Date(value).toLocaleTimeString('ru-RU') : '—'
const formatSize = value => {
  if (value === null || value === undefined) return '—'
  const units = ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ']
  let size = Number(value), index = 0
  while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1 }
  return `${size.toFixed(index > 1 ? 1 : 0)} ${units[index]}`
}

function ChannelStatus({ channel, onError }) {
  if (channel.last_error) return <button className="status status-error" onClick={onError}><AlertTriangle /> Ошибка проверки</button>
  if (!channel.enabled) return <span className="status muted">Отключён</span>
  if (channel.recording) return <span className="status recording">● Запись</span>
  if (channel.is_live) return <span className="status live">● В эфире</span>
  return <span className="status muted">● Не в эфире</span>
}

function ErrorModal({ details, onClose }) {
  if (!details) return null
  return <div className="modal-backdrop" role="presentation" onMouseDown={event => {
    if (event.target === event.currentTarget) onClose()
  }}>
    <section className="error-modal" role="dialog" aria-modal="true" aria-labelledby="error-title">
      <header className="modal-heading">
        <div className="modal-icon"><AlertTriangle /></div>
        <div><small>ДИАГНОСТИКА / ПОДРОБНОСТИ</small><h2 id="error-title">{details.title || 'Информация об ошибке'}</h2></div>
        <button className="icon modal-close" aria-label="Закрыть окно" title="Закрыть" onClick={onClose}><X /></button>
      </header>
      <div className="error-meta">
        {details.component && <span>Компонент: <b>{details.component}</b></span>}
        {details.timestamp && <span>Время: <b>{formatDate(details.timestamp)}</b></span>}
        {details.status && <span>HTTP: <b>{details.status}</b></span>}
        {details.recording_id && <span>Запись: <b>#{details.recording_id}</b></span>}
        {details.channel && <span>Канал: <b>@{details.channel}</b></span>}
        {details.exit_code !== null && details.exit_code !== undefined && <span>Код завершения: <b>{details.exit_code}</b></span>}
        {details.file && <span>Источник: <b>{details.file}:{details.line || 0}</b></span>}
      </div>
      <div className="error-section"><label>Сообщение</label><pre>{details.message || details.error || 'Подробности не переданы.'}</pre></div>
      {details.exception && <div className="error-section"><label>Traceback / stderr</label><pre className="stacktrace">{details.exception}</pre></div>}
      {details.extra && <div className="error-section"><label>Дополнительные данные</label><pre>{typeof details.extra === 'string' ? details.extra : JSON.stringify(details.extra, null, 2)}</pre></div>}
      <div className="modal-actions"><button className="primary" onClick={onClose}>Закрыть</button></div>
    </section>
  </div>
}

function DriveRing({ drive, selected, onClick }) {
  const percent = Math.min(100, Math.max(0, Number(drive.used_percent) || 0))
  return <button
    className={`drive-button ${selected ? 'selected' : ''}`}
    onClick={onClick}
    title={`${drive.name}: занято ${percent}%`}
    aria-label={`Информация о диске ${drive.name}`}
  >
    <span className="drive-ring" style={{ '--used': `${percent}%` }}><HardDrive /></span>
    <span className="drive-name">{drive.name}</span>
  </button>
}

export default function App() {
  const [channels, setChannels] = useState([])
  const [records, setRecords] = useState([])
  const [stats, setStats] = useState({})
  const [settings, setSettings] = useState({})
  const [settingsDirty, setSettingsDirty] = useState(false)
  const settingsDirtyRef = useRef(false)
  const [probeLogin, setProbeLogin] = useState('')
  const [storage, setStorage] = useState({ devices: [] })
  const [diagnostics, setDiagnostics] = useState(null)
  const [diagnosticTests, setDiagnosticTests] = useState({})
  const [login, setLogin] = useState('')
  const [filter, setFilter] = useState('all')
  const [tab, setTab] = useState('channels')
  const [consoleTab, setConsoleTab] = useState('logs')
  const [error, setError] = useState('')
  const [errorDetails, setErrorDetails] = useState(null)
  const [busy, setBusy] = useState(false)
  const [logs, setLogs] = useState([])
  const [logComponents, setLogComponents] = useState([])
  const [selectedComponents, setSelectedComponents] = useState([])
  const [logLevel, setLogLevel] = useState('ALL')
  const [drivePopover, setDrivePopover] = useState(null)
  const [storageBusy, setStorageBusy] = useState(false)
  const logCursor = useRef(0)
  const logBoxRef = useRef(null)

  const showError = useCallback((title, message, extra = {}) => {
    setError(message || title)
    setErrorDetails({ title, message, ...extra })
  }, [])

  const refresh = useCallback(async () => {
    try {
      const [channelData, recordingData, statusData, settingData, storageData] = await Promise.all([
        api('/channels'),
        api('/recordings'),
        api('/status'),
        api('/settings'),
        api('/storage'),
      ])
      setChannels(channelData)
      setRecords(recordingData)
      setStats(statusData)
      setSettings(current => ({
        ...settingData,
        ...(settingsDirtyRef.current ? { stream_quality: current.stream_quality } : {}),
      }))
      setStorage(storageData)
      setError('')
    } catch (exception) {
      setError(exception.message)
      setErrorDetails({
        title: 'Не удалось обновить данные интерфейса',
        message: exception.message,
        status: exception.status,
        exception: exception.detail ? JSON.stringify(exception.detail, null, 2) : null,
      })
    }
  }, [])

  const refreshDiagnostics = useCallback(async () => {
    try {
      setDiagnostics(await api('/diagnostics'))
    } catch (exception) {
      showError('Ошибка диагностики', exception.message, { status: exception.status })
    }
  }, [showError])

  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, 10000)
    return () => clearInterval(timer)
  }, [refresh])

  useEffect(() => {
    if (tab !== 'console') return undefined
    refreshDiagnostics()
    const timer = setInterval(refreshDiagnostics, 15000)
    return () => clearInterval(timer)
  }, [tab, refreshDiagnostics])

  useEffect(() => {
    if (tab !== 'console' || consoleTab !== 'logs') return undefined
    let alive = true
    let initialized = false
    logCursor.current = 0
    setLogs([])

    const poll = async () => {
      const params = new URLSearchParams({ limit: initialized ? '250' : '500', after: String(logCursor.current) })
      if (selectedComponents.length) params.set('components', selectedComponents.join(','))
      if (logLevel !== 'ALL') params.set('level', logLevel)
      try {
        const result = await api(`/logs?${params.toString()}`)
        if (!alive) return
        setLogComponents(result.components || [])
        if (!initialized) {
          setLogs(result.items || [])
          initialized = true
        } else if (result.items?.length) {
          setLogs(current => [...current, ...result.items].slice(-800))
        }
        logCursor.current = result.latest_id || logCursor.current
      } catch (exception) {
        if (alive) setError(exception.message)
      }
    }

    poll()
    const timer = setInterval(poll, 2000)
    return () => { alive = false; clearInterval(timer) }
  }, [tab, consoleTab, selectedComponents, logLevel])

  useEffect(() => {
    if (logBoxRef.current && logs.length) {
      logBoxRef.current.scrollTop = logBoxRef.current.scrollHeight
    }
  }, [logs])

  async function handleManualRefresh() {
    setBusy(true)
    try {
      const result = await api('/monitor/check', { method: 'POST' })
      await refresh()
      if (!result.ok) {
        showError('Ошибка проверки каналов Twitch', result.error || 'Не удалось обновить состояние каналов.', {
          component: 'monitor',
          extra: result,
        })
      }
    } catch (exception) {
      showError('Не удалось запустить проверку Twitch', exception.message, {
        status: exception.status,
        exception: exception.detail ? JSON.stringify(exception.detail, null, 2) : null,
      })
    } finally {
      setBusy(false)
    }
  }

  async function addChannel(event) {
    event.preventDefault()
    setBusy(true)
    try {
      await api('/channels', {
        method: 'POST',
        body: JSON.stringify({ login: login.trim().replace(/^@/, '') }),
      })
      setLogin('')
      await refresh()
    } catch (exception) {
      showError('Не удалось добавить канал', exception.message, { status: exception.status })
    } finally {
      setBusy(false)
    }
  }

  async function setChannelEnabled(channel, enabled) {
    try {
      await api(`/channels/${channel.id}`, { method: 'PATCH', body: JSON.stringify({ enabled }) })
      await refresh()
    } catch (exception) {
      showError('Не удалось изменить канал', exception.message, { status: exception.status })
    }
  }

  async function removeChannel(channel) {
    if (!window.confirm(`Удалить канал ${channel.login}? История записей будет удалена из базы, сами видеофайлы останутся на диске.`)) return
    try {
      await api(`/channels/${channel.id}`, { method: 'DELETE' })
      await refresh()
    } catch (exception) {
      showError('Не удалось удалить канал', exception.message, { status: exception.status })
    }
  }

  async function removeRecording(record) {
    if (!window.confirm('Удалить запись и соответствующий видеофайл?')) return
    try {
      await api(`/recordings/${record.id}`, { method: 'DELETE' })
      await refresh()
    } catch (exception) {
      showError('Не удалось удалить запись', exception.message, { status: exception.status })
    }
  }

  async function chooseStorage(deviceId) {
    setStorageBusy(true)
    try {
      const result = await api('/storage', {
        method: 'POST',
        body: JSON.stringify({ device_id: deviceId }),
      })
      setStorage(result)
      await refresh()
      setDrivePopover(null)
    } catch (exception) {
      showError('Не удалось изменить место хранения', exception.message, { status: exception.status })
    } finally {
      setStorageBusy(false)
    }
  }

  async function runDiagnostic(path, key) {
    setDiagnosticTests(current => ({ ...current, [key]: { loading: true } }))
    try {
      const result = await api(path, { method: 'POST' })
      setDiagnosticTests(current => ({ ...current, [key]: result }))
      await refreshDiagnostics()
      if (!result.ok) {
        showError(`Проверка Streamlink не пройдена`, result.error || 'Проверка завершилась ошибкой.', {
          component: key,
          extra: result,
        })
      }
    } catch (exception) {
      setDiagnosticTests(current => ({ ...current, [key]: { ok: false, error: exception.message } }))
      showError('Ошибка диагностической проверки', exception.message, { status: exception.status })
    }
  }

  async function runStreamlinkChannelProbe() {
    const loginToProbe = (probeLogin || channels[0]?.login || '').trim().replace(/^@/, '')
    if (!loginToProbe) {
      showError('Не выбран канал', 'Добавьте канал в мониторинг или выберите канал для проверки.')
      return
    }
    setProbeLogin(loginToProbe)
    setDiagnosticTests(current => ({ ...current, 'streamlink-channel': { loading: true } }))
    try {
      const result = await api('/diagnostics/streamlink/channel', {
        method: 'POST',
        body: JSON.stringify({ login: loginToProbe }),
      })
      setDiagnosticTests(current => ({ ...current, 'streamlink-channel': result }))
      if (!result.ok) {
        showError(`Streamlink не смог проверить @${loginToProbe}`, result.error || 'Проверка завершилась ошибкой.', {
          component: 'monitor',
          channel: loginToProbe,
          exit_code: result.exit_code,
          exception: result.stderr,
          extra: result,
        })
      }
      await refreshDiagnostics()
    } catch (exception) {
      setDiagnosticTests(current => ({ ...current, 'streamlink-channel': { ok: false, error: exception.message } }))
      showError('Ошибка проверки канала через Streamlink', exception.message, { status: exception.status })
    }
  }

  function changeStreamQuality(value) {
    settingsDirtyRef.current = true
    setSettingsDirty(true)
    setSettings(current => ({ ...current, stream_quality: value }))
  }

  async function saveStreamQuality(event) {
    event.preventDefault()
    setBusy(true)
    try {
      const saved = await api('/settings', {
        method: 'PUT',
        body: JSON.stringify({ stream_quality: settings.stream_quality }),
      })
      settingsDirtyRef.current = false
      setSettingsDirty(false)
      setSettings(saved)
      setError('')
      setErrorDetails(null)
    } catch (exception) {
      showError('Не удалось сохранить качество записи', exception.message, {
        status: exception.status,
        exception: exception.detail ? JSON.stringify(exception.detail, null, 2) : null,
      })
    } finally {
      setBusy(false)
    }
  }

  function showLogDetails(entry) {
    setErrorDetails({
      title: `${entry.level}: ${entry.component}`,
      timestamp: entry.timestamp,
      component: entry.component,
      message: entry.message,
      exception: entry.exception,
      file: entry.file,
      line: entry.line,
      channel: entry.channel,
      recording_id: entry.recording_id,
      exit_code: entry.exit_code,
      extra: {
        log_id: entry.id,
        function: entry.function,
        channel_id: entry.channel_id,
      },
    })
  }

  function toggleComponent(name) {
    setSelectedComponents(current => current.includes(name)
      ? current.filter(item => item !== name)
      : [...current, name])
  }

  const filteredChannels = useMemo(() => channels.filter(channel => {
    if (filter === 'live') return channel.is_live && channel.enabled
    if (filter === 'recording') return channel.recording
    if (filter === 'offline') return !channel.is_live
    return true
  }), [channels, filter])

  const driveInPopover = storage.devices?.find(item => item.id === drivePopover)
  const selectedDrive = storage.devices?.find(item => item.id === storage.selected_id)

  return <div className="layout">
    <aside>
      <div className="brand"><span><Radio /></span><div><b>Twitch Archive</b><small>STREAM MONITOR</small></div></div>
      <label className="sidebar-label">РАБОЧАЯ ОБЛАСТЬ</label>
      <button className={`nav ${tab === 'channels' ? 'active' : ''}`} onClick={() => setTab('channels')}><Tv />Каналы</button>
      <button className={`nav ${tab === 'records' ? 'active' : ''}`} onClick={() => setTab('records')}><Archive />Архив <em>{stats.recordings || 0}</em></button>
      <button className={`nav ${tab === 'console' ? 'active' : ''}`} onClick={() => setTab('console')}><Terminal />Консоль</button>
      <button className={`nav ${tab === 'settings' ? 'active' : ''}`} onClick={() => setTab('settings')}><Settings />Настройки</button>
      <div className="sidebottom">
        <label className="sidebar-label">СИСТЕМА</label>
        <p><i className={stats.monitor?.running ? 'online-dot' : 'offline-dot'} /> {stats.monitor?.running ? 'Мониторинг активен' : 'Мониторинг остановлен'}</p>
        <small>Опрос: {settings.poll_interval_seconds || '—'} сек.</small>
        <small>Последняя проверка: {formatTime(stats.monitor?.last_check_at)}</small>
      </div>
    </aside>

    <main>
      <header>
        <div><small>TWITCH / ARCHIVE</small><h1>{tab === 'channels' ? 'Мониторинг каналов' : tab === 'records' ? 'Архив трансляций' : tab === 'console' ? 'Консоль и диагностика' : 'Настройки хранения'}</h1></div>
        <button className={`icon refresh-button ${busy ? 'spinning' : ''}`} onClick={tab === 'channels' ? handleManualRefresh : refresh} title={tab === 'channels' ? 'Проверить эфиры Twitch сейчас' : 'Обновить данные'} disabled={busy}><RefreshCw /></button>
      </header>

      {error && <div className="error" role="alert">
        <button className="error-message" onClick={() => setErrorDetails(errorDetails || { title: 'Ошибка', message: error })}>{error}</button>
        <button onClick={() => setError('')} aria-label="Скрыть уведомление"><X /></button>
      </div>}

      <section className="stats">
        <article><Tv /><b>{stats.channels || 0}</b><small>Каналов</small></article>
        <article><CircleDot /><b>{stats.live || 0}</b><small>В эфире</small></article>
        <article><Activity /><b>{stats.recording || 0}</b><small>Идёт запись</small></article>
        <article><Archive /><b>{stats.recordings || 0}</b><small>Всего записей</small></article>
      </section>

      {tab === 'channels' && <>
        <section className="panel">
          <div className="heading"><div><h2>Отслеживаемые каналы</h2><p>Добавьте Twitch-логин для автоматического мониторинга.</p></div><span>{channels.length} каналов</span></div>
          <form onSubmit={addChannel}>
            <div className="input"><b>@</b><input value={login} onChange={event => setLogin(event.target.value)} placeholder="twitch_channel" maxLength="25" pattern="[A-Za-z0-9_]+" required /></div>
            <button className="primary" disabled={busy}><Plus />Добавить канал</button>
          </form>
          <div className="filters">{[['all', 'Все'], ['live', 'В эфире'], ['recording', 'Записываются'], ['offline', 'Не в эфире']].map(([key, label]) => <button key={key} className={filter === key ? 'chosen' : ''} onClick={() => setFilter(key)}>{label}</button>)}</div>
          {filteredChannels.length === 0 ? <div className="empty"><Tv /><b>{channels.length ? 'Нет каналов в этой категории' : 'Каналов пока нет'}</b><small>{channels.length ? 'Измените фильтр.' : 'Добавьте канал выше.'}</small></div> : filteredChannels.map(channel => <div className="channel" key={channel.id}>
            <div className="avatar">{(channel.display_name || channel.login)[0].toUpperCase()}</div>
            <div className="channeltext">
              <b>{channel.display_name || channel.login} <small>@{channel.login}</small></b>
              <p>{channel.stream_title || 'Ожидание трансляции'}{channel.game_name ? ` · ${channel.game_name}` : ''}</p>
              {channel.last_error && <button className="channel-error" onClick={() => setErrorDetails({
                title: `Ошибка проверки @${channel.login}`,
                component: 'monitor',
                timestamp: channel.last_checked_at,
                message: channel.last_error,
                channel: channel.login,
                extra: { channel_id: channel.id, last_checked_at: channel.last_checked_at },
              })}>Ошибка API — открыть подробности</button>}
              {channel.recording_process?.last_error && <button className="channel-error" onClick={() => setErrorDetails({
                title: `Ошибка Streamlink @${channel.login}`,
                component: 'recorder',
                message: channel.recording_process.last_error,
                channel: channel.login,
                extra: channel.recording_process,
              })}>Последняя ошибка записи — подробности</button>}
            </div>
            <div className="channelstatus"><ChannelStatus channel={channel} onError={() => setErrorDetails({
              title: `Ошибка проверки @${channel.login}`,
              component: 'monitor',
              timestamp: channel.last_checked_at,
              message: channel.last_error,
              channel: channel.login,
            })} />{channel.is_live && channel.viewer_count !== null && channel.viewer_count !== undefined && <small>{channel.viewer_count} зрителей</small>}</div>
            <input className="toggle" type="checkbox" aria-label={`Мониторинг ${channel.login}`} checked={Boolean(channel.enabled)} onChange={event => setChannelEnabled(channel, event.target.checked)} />
            <button className="icon" onClick={() => removeChannel(channel)} title="Удалить канал"><Trash2 /></button>
          </div>)}
        </section>
        <div className="notice"><HardDrive /><div><b>Хранение без автоматического удаления</b><p>Архив не очищается автоматически. Следите за свободным местом на выбранном диске.</p></div></div>
        <div className="monitor-summary">
          <span>Последняя проверка: <b>{formatDate(stats.monitor?.last_check_at)}</b></span>
          <span>Успешная проверка: <b>{formatDate(stats.monitor?.last_success_at)}</b></span>
          <span>Ошибок подряд: <b>{stats.monitor?.consecutive_failures ?? 0}</b></span>
          {stats.monitor?.last_error && <button onClick={() => setErrorDetails({ title: 'Последняя ошибка мониторинга', component: 'monitor', message: stats.monitor.last_error })}>Подробнее об ошибке</button>}
        </div>
      </>}

      {tab === 'records' && <section className="panel">
        <div className="heading"><div><h2>Записанные трансляции</h2><p>Файлы, созданные Streamlink. Нажмите на ошибку, чтобы увидеть stderr и код завершения.</p></div><span>{records.length} записей</span></div>
        {records.length === 0 ? <div className="empty"><Archive /><b>Архив пока пуст</b><small>Записи появятся после обнаружения эфиров.</small></div> : records.map(record => <div className="record" key={record.id}>
          <div className="thumb"><Tv /></div>
          <div className="channeltext">
            <b>{record.title || `Трансляция ${record.login}`}</b>
            <p>@{record.login} · {formatDate(record.started_at)}</p>
            <small>{record.game_name || 'Без категории'} · {formatSize(record.file_size)}</small>
            {record.error && <button className="record-error" onClick={() => setErrorDetails({
              title: `Ошибка записи #${record.id}`,
              component: 'recorder',
              timestamp: record.ended_at || record.started_at,
              message: record.error,
              channel: record.login,
              recording_id: record.id,
              extra: { status: record.status, file_path: record.file_path, file_size: record.file_size },
            })}>Открыть подробности ошибки</button>}
            {!record.file_exists && record.file_path && record.status !== 'starting' && record.status !== 'recording' && <small className="missing-file">Файл недоступен (возможно, диск отключён)</small>}
          </div>
          <button className={`pill ${record.status} ${record.error ? 'clickable' : ''}`} onClick={() => record.error && setErrorDetails({
            title: `Статус записи #${record.id}: ${record.status}`,
            component: 'recorder',
            timestamp: record.ended_at || record.started_at,
            message: record.error,
            channel: record.login,
            recording_id: record.id,
          })}>{({ starting: 'Запуск', recording: 'Запись', completed: 'Готово', error: 'Ошибка', interrupted: 'Прервана', stopped: 'Остановлена' })[record.status] || record.status}</button>
          {record.file_exists && <a className="icon" href={record.download_url} title="Скачать запись"><Download /></a>}
          {record.status !== 'recording' && record.status !== 'starting' && <button className="icon" onClick={() => removeRecording(record)} title="Удалить запись"><Trash2 /></button>}
        </div>)}
      </section>}

      {tab === 'console' && <section className="panel console-panel">
        <div className="heading console-heading">
          <div><h2>Системная консоль</h2><p>Журнал backend-компонентов и диагностическое состояние сервиса.</p></div>
          <span className="console-live"><i /> LIVE</span>
        </div>
        <div className="console-tabs">
          <button className={consoleTab === 'logs' ? 'chosen' : ''} onClick={() => setConsoleTab('logs')}><Terminal />Логи</button>
          <button className={consoleTab === 'diagnostics' ? 'chosen' : ''} onClick={() => setConsoleTab('diagnostics')}><Activity />Диагностика</button>
        </div>

        {consoleTab === 'logs' && <>
          <div className="log-toolbar">
            <details className="component-filter">
              <summary><ChevronDown />Компоненты {selectedComponents.length ? `(${selectedComponents.length})` : '(все)'}</summary>
              <div className="component-options">
                <button onClick={() => setSelectedComponents([])}>Все компоненты</button>
                {logComponents.map(name => <label key={name}><input type="checkbox" checked={selectedComponents.includes(name)} onChange={() => toggleComponent(name)} />{name}</label>)}
              </div>
            </details>
            <select value={logLevel} onChange={event => setLogLevel(event.target.value)} aria-label="Минимальный уровень логирования">
              <option value="ALL">Все уровни</option>
              <option value="DEBUG">DEBUG и выше</option>
              <option value="INFO">INFO и выше</option>
              <option value="SUCCESS">SUCCESS и выше</option>
              <option value="WARNING">WARNING и выше</option>
              <option value="ERROR">ERROR и выше</option>
            </select>
            <button className="text-button" onClick={() => setLogs([])}>Очистить экран</button>
            <button className="text-button" onClick={() => navigator.clipboard?.writeText(logs.map(item => `[${item.timestamp}] [${item.level}] [${item.component}] ${item.message}${item.exception ? `\n${item.exception}` : ''}`).join('\n'))}><Clipboard />Копировать</button>
          </div>
          <div className="log-console" ref={logBoxRef}>
            {logs.length === 0 ? <div className="log-empty">Ожидание новых сообщений...</div> : logs.map(entry => <button
              key={entry.id}
              className={`log-line level-${entry.level.toLowerCase()} ${entry.levelno >= 30 ? 'has-details' : ''}`}
              onClick={() => entry.levelno >= 30 && showLogDetails(entry)}
              title={entry.levelno >= 30 ? 'Нажмите, чтобы открыть подробности' : ''}
            >
              <time>{formatTime(entry.timestamp)}</time>
              <b className="log-level">{entry.level}</b>
              <span className="log-component">{entry.component}</span>
              {entry.channel && <span className="log-channel">@{entry.channel}</span>}
              <span className="log-message">{entry.message}</span>
              {entry.levelno >= 30 && <AlertTriangle className="log-alert" />}
            </button>)}
          </div>
          <div className="log-footer"><span>В памяти: последние 5000 событий · отображается {logs.length}</span><span>Нажмите на WARNING/ERROR для деталей</span></div>
        </>}

        {consoleTab === 'diagnostics' && <div className="diagnostics-view">
          <div className="diagnostic-actions">
            <button className="primary" onClick={() => runDiagnostic('/diagnostics/streamlink', 'streamlink')}><Activity />Проверить Streamlink</button>
            <select
              className="probe-select"
              aria-label="Канал для проверки"
              value={probeLogin || channels[0]?.login || ''}
              onChange={event => setProbeLogin(event.target.value)}
            >
              <option value="">Выберите канал…</option>
              {channels.map(channel => <option key={channel.id} value={channel.login}>@{channel.login}</option>)}
            </select>
            <button className="secondary" onClick={runStreamlinkChannelProbe} disabled={!channels.length}><Wifi />Проверить эфир</button>
            <button className="secondary" onClick={refreshDiagnostics}><RefreshCw />Обновить диагностику</button>
          </div>
          {Object.entries(diagnosticTests).map(([key, result]) => <div className={`test-result ${result.loading ? 'loading' : result.ok ? 'passed' : 'failed'}`} key={key}>
            {result.loading ? 'Проверка выполняется…' : result.ok ? 'Проверка пройдена' : 'Проверка не пройдена'} · {key === 'streamlink-channel' ? `Streamlink @${result.channel || probeLogin}` : 'Streamlink'}
            {!result.loading && <span>{result.version || result.error || (key === 'streamlink-channel' ? result.live ? `эфир найден · ${(result.available_qualities || []).join(', ')}` : 'эфир не обнаружен' : '')}</span>}
          </div>)}
          {!diagnostics ? <div className="empty"><Activity /><b>Загрузка диагностики…</b></div> : <>
            <div className="diagnostic-grid">
              <article><Database /><small>База данных</small><b className={diagnostics.database?.accessible ? 'good' : 'bad'}>{diagnostics.database?.accessible ? 'Доступна' : 'Ошибка'}</b><span>{diagnostics.database?.journal_mode || '—'} · {diagnostics.database?.recordings ?? 0} записей</span></article>
              <article><Wifi /><small>Обнаружение эфиров</small><b className="good">Streamlink</b><span>Helix API и Client ID/Secret не используются</span></article>
              <article><Activity /><small>Streamlink + Chromium</small><b className={diagnostics.streamlink?.available && diagnostics.streamlink?.chromium_available ? 'good' : 'bad'}>{diagnostics.streamlink?.available ? diagnostics.streamlink?.chromium_available ? 'Готовы' : 'Нет Chromium' : 'Streamlink не найден'}</b><span>{diagnostics.streamlink?.version || 'Версия не определена'}</span></article>
              <article><HardDrive /><small>Хранилище</small><b className={diagnostics.storage?.available ? 'good' : 'bad'}>{diagnostics.storage?.available ? 'Доступно' : 'Недоступно'}</b><span>{diagnostics.storage?.path || diagnostics.storage?.error || '—'}</span></article>
            </div>
            <div className="diagnostic-block">
              <h3>Цикл мониторинга</h3>
              <div><span>Метод обнаружения</span><b>Streamlink Twitch plugin</b></div>
              <div><span>Проверка Helix API</span><b>Не используется</b></div>
              <div><span>Таймаут одной проверки</span><b>{diagnostics.monitor?.probe_timeout_seconds ?? '—'} сек.</b></div>
              <div><span>Параллельных проверок</span><b>{diagnostics.monitor?.max_parallel_checks ?? '—'}</b></div>
              <div><span>Последняя попытка</span><b>{formatDate(diagnostics.monitor?.last_check_at)}</b></div>
              <div><span>Последняя успешная проверка</span><b>{formatDate(diagnostics.monitor?.last_success_at)}</b></div>
              <div><span>Ошибок подряд</span><b>{diagnostics.monitor?.consecutive_failures ?? 0}</b></div>
              <div><span>Интервал</span><b>{diagnostics.monitor?.interval_seconds ?? '—'} сек.</b></div>
              {diagnostics.monitor?.last_error && <button className="channel-error" onClick={() => setErrorDetails({ title: 'Последняя ошибка мониторинга', component: 'monitor', message: diagnostics.monitor.last_error })}>Показать последнюю ошибку</button>}
            </div>
            <div className="diagnostic-block">
              <h3>Активные процессы Streamlink</h3>
              {diagnostics.streamlink?.processes?.length ? diagnostics.streamlink.processes.map(item => <div key={item.channel_id}><span>channel_id={item.channel_id} · PID {item.pid}</span><b>{item.return_code === null ? 'Работает' : `Код ${item.return_code}`} · запись #{item.recording_id}</b></div>) : <p>Активных процессов нет.</p>}
            </div>
            <details className="raw-diagnostics"><summary>Полный диагностический отчёт (JSON)</summary><pre>{JSON.stringify(diagnostics, null, 2)}</pre></details>
          </>}
        </div>}
      </section>}

      {tab === 'settings' && <section className="panel settings-panel">
        <div className="heading"><div><h2>Место хранения записей</h2><p>Выберите диск. Сервис будет сохранять видео в каталоге recordings в корне выбранного раздела.</p></div><span>{storage.mode === 'external' ? 'Внешний диск' : 'Внутренний диск'}</span></div>
        <div className="storage-choice">
          <label className={`storage-option ${!storage.selected_id ? 'active' : ''}`}>
            <input type="radio" name="storage" checked={!storage.selected_id} onChange={() => chooseStorage(null)} disabled={storageBusy} />
            <HardDrive />
            <span><b>Внутренний каталог</b><small>/recordings внутри контейнера (volume ./recordings на хосте)</small></span>
            <em>По умолчанию</em>
          </label>
          {storage.devices?.map(drive => <label className={`storage-option ${storage.selected_id === drive.id ? 'active' : ''}`} key={drive.id}>
            <input type="radio" name="storage" checked={storage.selected_id === drive.id} onChange={() => chooseStorage(drive.id)} disabled={storageBusy} />
            <HardDrive />
            <span><b>{drive.name}</b><small>{drive.mountpoint} · {drive.filesystem} · {formatSize(drive.free_bytes)} свободно</small></span>
            <em>{drive.used_percent}% занято</em>
          </label>)}
          {!storage.devices?.length && <div className="empty compact"><HardDrive /><b>Внешние диски не обнаружены</b><small>Подключите диск и смонтируйте его в /mnt или /media на хосте. Список обновится автоматически.</small></div>}
        </div>
        <div className={`storage-current ${storage.available ? 'good' : 'bad'}`}>
          <b>{storage.available ? 'Каталог хранения доступен' : 'Каталог хранения недоступен'}</b>
          <span>{storage.display_path || storage.path || storage.error || '—'}</span>
          {storage.error && <p>{storage.error} Запись не будет перенаправлена на системный диск.</p>}
        </div>
        <div className="notice"><AlertTriangle /><div><b>Безопасность внешнего диска</b><p>Приложение обнаруживает уже смонтированные разделы, но не монтирует их само. Если выбранный диск отключить, новые записи остановятся с ошибкой вместо скрытой записи во внутреннюю файловую систему.</p></div></div>
        <form className="recording-settings-form" onSubmit={saveStreamQuality}>
          <div className="heading"><div><h2>Качество записи</h2><p>Выбранное качество применяется ко всем новым записям. Уже запущенные процессы не меняются.</p></div><span>{settings.stream_quality || 'best'}</span></div>
          <label>Поток Twitch
            <select value={settings.stream_quality || 'best'} onChange={event => changeStreamQuality(event.target.value)}>
              {['best', '1080p60', '1080p', '720p60', '720p', '480p', '360p', 'worst'].map(value =>
                <option key={value} value={value}>{value === 'best' ? 'Лучшее доступное' : value === 'worst' ? 'Худшее доступное' : value}</option>
              )}
            </select>
            <small>Список фактически доступных вариантов зависит от трансляции. Streamlink не перекодирует видео, а выбирает готовый вариант Twitch.</small>
          </label>
          <button className="primary" type="submit" disabled={busy || !settingsDirty}><Settings />Сохранить качество</button>
        </form>
        <div className="diagnostic-block">
          <h3>Настройки мониторинга</h3>
          <div><span>Интервал проверки</span><b>{settings.poll_interval_seconds || '—'} сек.</b></div>
          <div><span>Таймаут проверки Streamlink</span><b>{settings.stream_check_timeout_seconds || '—'} сек.</b></div>
          <div><span>Способ обнаружения</span><b>Streamlink, без Helix</b></div>
          <div><span>Автоматическая очистка архива</span><b>Отключена</b></div>
        </div>
      </section>}

      <footer>Twitch Archive 1.1.0 <span>React · FastAPI · Streamlink · Structured logging</span></footer>
    </main>

    {storage.devices?.length > 0 && <div className="drive-dock">
      <span className="dock-title">ДИСКИ</span>
      {storage.devices.map(drive => <DriveRing
        key={drive.id}
        drive={drive}
        selected={storage.selected_id === drive.id}
        onClick={() => setDrivePopover(current => current === drive.id ? null : drive.id)}
      />)}
      {driveInPopover && <div className="drive-popover">
        <button className="icon popover-close" aria-label="Закрыть" onClick={() => setDrivePopover(null)}><X /></button>
        <b>{driveInPopover.name}</b>
        <small>{driveInPopover.source} · {driveInPopover.filesystem}</small>
        <div className="drive-usage"><span>Занято</span><b>{formatSize(driveInPopover.used_bytes)} / {formatSize(driveInPopover.total_bytes)}</b></div>
        <div className="usage-track"><i style={{ width: `${driveInPopover.used_percent}%` }} /></div>
        <div className="drive-usage"><span>Свободно</span><b>{formatSize(driveInPopover.free_bytes)} ({100 - driveInPopover.used_percent}%)</b></div>
        <div className="drive-mount"><span>Точка монтирования</span><b>{driveInPopover.mountpoint}</b></div>
        {storage.selected_id !== driveInPopover.id && <button className="primary use-drive" onClick={() => chooseStorage(driveInPopover.id)} disabled={storageBusy}>Выбрать для записи</button>}
        {storage.selected_id === driveInPopover.id && <span className="selected-storage-label"><CheckCircle2 /> Используется для записи</span>}
      </div>}
    </div>}

    <ErrorModal details={errorDetails} onClose={() => setErrorDetails(null)} />
  </div>
}
