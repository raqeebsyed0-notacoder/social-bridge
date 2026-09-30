/**
 * Social-Bridge — Desktop Plugin (permanent, like hermes-newswire)
 * Hybrid: local browser for X/LinkedIn (1Password/vault autofill in headed window) + euphoria API for rest.
 */
import {
  host,
  queryClient,
  useQuery,
  useMutation,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  PALETTE_AREA,
} from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useEffect, useState } from 'react'

const ID = 'social-bridge'
let rest = null
let storageGet = null
let storageSet = null
let osOpen = null

function ensureStyles() {
  const css = `
  .${ID}-page { display:flex; flex-direction:column; height:100%; min-height:0; background: var(--ui-bg-editor); }
  .${ID}-tabs { display:flex; gap:.5rem; padding:.5rem 1rem; border-bottom:1px solid var(--ui-stroke-secondary); flex-wrap:wrap; }
  .${ID}-tab { background:none; border:0; padding:.35rem .6rem; font-size:.8rem; color:var(--ui-text-secondary); cursor:pointer; border-radius:.3rem; font-family:inherit; }
  .${ID}-tab[data-active="1"]{ background:var(--ui-bg-tertiary); color:var(--ui-text-primary); font-weight:600; }
  .${ID}-list { display:flex; flex-direction:column; }
  .${ID}-row { display:flex; gap:.75rem; padding:.6rem 1rem; border-bottom:1px solid var(--ui-stroke-tertiary); }
  .${ID}-row:hover{ background:var(--ui-bg-tertiary); }
  .${ID}-meta{ font-size:.7rem; color:var(--ui-text-quaternary); display:flex; gap:.4rem; flex-wrap:wrap;}
  .${ID}-src{ color:var(--ui-accent); font-weight:600; text-transform:uppercase; font-size:.65rem; letter-spacing:.06em;}
  .${ID}-card { margin:12px 16px; padding:14px; border:1px solid var(--ui-stroke-secondary); border-radius:8px; background: var(--ui-bg-tertiary); display:flex; flex-direction:column; gap:10px; }
  .${ID}-hint { font-size:.75rem; color:var(--ui-text-tertiary); line-height:1.4; }
  `.trim()
  let s = document.getElementById(`${ID}-styles`)
  if (!s) { s = document.createElement('style'); s.id = `${ID}-styles`; document.head.appendChild(s) }
  if (s.textContent !== css) s.textContent = css
}

function timeAgo(iso){
  const t=Date.parse(iso); if(!isFinite(t)) return ''; const s=Math.max(0,(Date.now()-t)/1000);
  if(s<60) return 'now'; if(s<3600) return `${Math.floor(s/60)}m`; if(s<86400) return `${Math.floor(s/3600)}h`; return `${Math.floor(s/86400)}d`
}

function SettingsPane(){
  const authX = useQuery({ queryKey:[ID,'auth','x'], queryFn:()=> rest('/auth/status?platform=x')})
  const authLi = useQuery({ queryKey:[ID,'auth','linkedin'], queryFn:()=> rest('/auth/status?platform=linkedin')})
  const settingsQ = useQuery({ queryKey:[ID,'settings'], queryFn:()=> rest('/settings').then(r=>r.settings||r)})
  const [poll,setPoll]=useState('')
  const [ytKey,setYtKey]=useState('')
  useEffect(()=>{
    if(settingsQ.data){
      if(settingsQ.data.poll_interval) setPoll(String(settingsQ.data.poll_interval))
      if(settingsQ.data.token_youtube) setYtKey(String(settingsQ.data.token_youtube))
    }
  }, [settingsQ.data?.poll_interval, settingsQ.data?.token_youtube])

  const saveSettings = useMutation({
    mutationFn: (patch)=> rest('/settings',{method:'PATCH', body: patch}),
    onSuccess: ()=> queryClient.invalidateQueries({queryKey:[ID,'settings']}),
    onError: (e)=> host.notify({kind:'error', message: String(e.message||e).slice(0,300)})
  })
  const login = useMutation({
    mutationFn: async (plat)=>{
      const urls={x:'https://x.com/login', linkedin:'https://www.linkedin.com/login'}
      const url=urls[plat]||`https://${plat}.com`
      // 1) Open in Hermes preview rail - the permanent browser
      try{ await rest('/preview', {method:'POST', body:{url}}); }catch(_){}
      // 2) Seed browser_data in background for headless polling (non-blocking)
      try{ rest(`/auth/login?platform=${plat}`,{method:'POST'}).catch(()=>{}); }catch(_){}
      return {opened:true, url, via:'preview-rail'}
    },
    onSuccess: (data, plat)=> {
      host.notify({kind:'info', message: `Opened ${plat} in preview rail — log in there with 1Password/vault. Preview rail is the permanent browser.`})
      setTimeout(()=> queryClient.invalidateQueries({queryKey:[ID,'auth']}), 3000)
    },
    onError: (e)=> host.notify({kind:'error', message: `Login failed: ${String(e.message||e).slice(0,280)}`})
  })

  return jsxs('div',{style:{display:'flex', flexDirection:'column', gap:12, padding:'12px 0'}, children:[
    jsxs('div',{className:`${ID}-card`, children:[
      jsx('div',{style:{fontWeight:600, fontSize:'.9rem'}, children:'Browser Auth — X / LinkedIn (preview rail)'}),
      jsx('div',{className:`${ID}-hint`, children:'Click Login → opens in Hermes preview rail (permanent browser, like Newswire). Log in there with 1Password/vault. Agent will only use drive_preview/desktop_preview on that rail — no external Chrome. Close rail preview after login — cookies persist in browser_data for headless polling.'}),
      jsxs('div',{style:{display:'flex', gap:8, flexWrap:'wrap'}, children:[
        jsxs('div',{style:{flex:'1 1 220px', border:'1px solid var(--ui-stroke-secondary)', borderRadius:6, padding:10, display:'flex', flexDirection:'column', gap:6}, children:[
          jsxs('div',{style:{display:'flex', justifyContent:'space-between', alignItems:'center'}, children:[
            jsx('span',{style:{fontWeight:600}, children:'X (x.com)'}),
            jsx('span',{style:{fontSize:'.7rem', padding:'2px 6px', borderRadius:99, background: authX.data?.logged_in ? 'var(--ui-success, #2d7)' : 'var(--ui-bg-elevated)', color: authX.data?.logged_in ? '#fff':'var(--ui-text-tertiary)'}, children: authX.isLoading?'…': authX.data?.logged_in?'Logged in':'Not logged in'})
          ]}),
          jsx('div',{className:`${ID}-hint`, children: authX.data?.error ? `Error: ${authX.data.error}` : 'Preview rail is the permanent browser — one login survives reboots.'}),
          jsx('button',{onClick:()=> login.mutate('x'), disabled: login.isPending, style:{padding:'6px 10px', background:'var(--ui-accent)', color:'#fff', border:0, borderRadius:6, cursor:'pointer'}, children: login.isPending? 'Opening…':'Login X (preview rail)'}),
        ]}),
        jsxs('div',{style:{flex:'1 1 220px', border:'1px solid var(--ui-stroke-secondary)', borderRadius:6, padding:10, display:'flex', flexDirection:'column', gap:6}, children:[
          jsxs('div',{style:{display:'flex', justifyContent:'space-between', alignItems:'center'}, children:[
            jsx('span',{style:{fontWeight:600}, children:'LinkedIn'}),
            jsx('span',{style:{fontSize:'.7rem', padding:'2px 6px', borderRadius:99, background: authLi.data?.logged_in ? 'var(--ui-success, #2d7)' : 'var(--ui-bg-elevated)', color: authLi.data?.logged_in ? '#fff':'var(--ui-text-tertiary)'}, children: authLi.isLoading?'…': authLi.data?.logged_in?'Logged in':'Not logged in'})
          ]}),
          jsx('div',{className:`${ID}-hint`, children: authLi.data?.error ? `Error: ${authLi.data.error}` : 'Preview rail is the permanent browser — 1Password/vault there.'}),
          jsx('button',{onClick:()=> login.mutate('linkedin'), disabled: login.isPending, style:{padding:'6px 10px', background:'var(--ui-accent)', color:'#fff', border:0, borderRadius:6, cursor:'pointer'}, children: login.isPending? 'Opening…':'Login LinkedIn (preview rail)'}),
        ]}),
      ]}),
      login.isSuccess ? jsx('div',{className:`${ID}-hint`, style:{color:'var(--ui-accent)'}, children:'Opened in preview rail — log in there, then close preview. Status flips to Logged in.'}) : null,
    ]}),
    jsxs('div',{className:`${ID}-card`, children:[
      jsx('div',{style:{fontWeight:600}, children:'Polling & API Tokens (Euphoria 24/7)'}),
      jsxs('div',{style:{display:'flex', gap:8, alignItems:'center', flexWrap:'wrap'}, children:[
        jsx('span',{style:{fontSize:'.8rem', minWidth:120}, children:'Poll interval (sec)'}),
        jsx('input',{value:poll, onChange:e=>setPoll(e.target.value), placeholder:'900', style:{width:100, padding:'6px 8px', border:'1px solid var(--ui-stroke-secondary)', borderRadius:6, background:'var(--ui-bg-elevated)'}}),
        jsx('button',{onClick:()=> saveSettings.mutate({poll_interval: Number(poll)||900}), style:{padding:'6px 10px', border:'1px solid var(--ui-accent)', color:'var(--ui-accent)', background:'none', borderRadius:6, cursor:'pointer'}, children:'Save'}),
      ]}),
      jsxs('div',{style:{display:'flex', gap:8, alignItems:'center', flexWrap:'wrap'}, children:[
        jsx('span',{style:{fontSize:'.8rem', minWidth:120}, children:'YouTube API key'}),
        jsx('input',{value:ytKey, onChange:e=>setYtKey(e.target.value), placeholder:'AIza… (optional, for youtube search)', type:'password', style:{flex:'1 1 260px', padding:'6px 8px', border:'1px solid var(--ui-stroke-secondary)', borderRadius:6, background:'var(--ui-bg-elevated)'}}),
        jsx('button',{onClick:()=> saveSettings.mutate({token_youtube: ytKey}), style:{padding:'6px 10px', border:'1px solid var(--ui-accent)', color:'var(--ui-accent)', background:'none', borderRadius:6, cursor:'pointer'}, children:'Save token'}),
      ]}),
      jsx('div',{className:`${ID}-hint`, children:'Tokens stored in dashboard/data.db settings (not vault). X/LinkedIn never use this field — they use vault autofill in the headed window above.'}),
      saveSettings.isSuccess ? jsx('div',{style:{fontSize:'.75rem', color:'var(--ui-success, #2d7)'}, children:'Saved.'}) : null,
    ]}),
  ]})
}

function WatchlistPane(){
  const listQ = useQuery({ queryKey:[ID,'watchlist'], queryFn:()=> rest('/watchlist').then(r=>r.watchlist||[]) })
  const [q,setQ]=useState('')
  const [plat,setPlat]=useState('x')
  const add = useMutation({
    mutationFn: ()=> rest('/watchlist',{method:'POST', body:{platform:plat, query:q, limit:12}}),
    onSuccess: ()=> { setQ(''); queryClient.invalidateQueries({queryKey:[ID,'watchlist']}); queryClient.invalidateQueries({queryKey:[ID,'posts']})}
  })
  const del = useMutation({
    mutationFn: (id)=> rest(`/watchlist/${id}`,{method:'DELETE'}),
    onSuccess: ()=> queryClient.invalidateQueries({queryKey:[ID,'watchlist']})
  })
  return jsxs('div',{style:{display:'flex', flexDirection:'column', gap:12, padding:12}, children:[
    jsxs('div',{style:{display:'flex', gap:8, flexWrap:'wrap', alignItems:'center'}, children:[
      jsx('select',{value:plat, onChange:e=>setPlat(e.target.value), style:{padding:'6px 8px', border:'1px solid var(--ui-stroke-secondary)', borderRadius:6, background:'var(--ui-bg-elevated)'}, children:[
        jsx('option',{value:'x', children:'X'}),
        jsx('option',{value:'linkedin', children:'LinkedIn'}),
        jsx('option',{value:'youtube', children:'YouTube'}),
        jsx('option',{value:'instagram', children:'Instagram'}),
      ]}),
      jsx('input',{value:q, onChange:e=>setQ(e.target.value), placeholder:'Query e.g. AI design (adds to watchlist → polled every poll_interval)', style:{flex:'1 1 260px', padding:'6px 10px', border:'1px solid var(--ui-stroke-secondary)', borderRadius:6, background:'var(--ui-bg-elevated)'}}),
      jsx('button',{onClick:()=> q.trim() && add.mutate(), disabled:add.isPending||!q.trim(), style:{padding:'6px 12px', background:'var(--ui-accent)', color:'#fff', border:0, borderRadius:6, cursor:'pointer'}, children: add.isPending?'Adding…':'Add to Watchlist'}),
    ]}),
    listQ.isLoading ? jsx('div',{className:`${ID}-hint`, children:'Loading watchlist…'}) :
    (listQ.data||[]).length===0 ? jsx('div',{className:`${ID}-hint`, children:'No watchlist yet — add one above. Poll loop runs every poll_interval in background (like Newswire).'}) :
    jsx('div',{style:{display:'flex', flexDirection:'column', gap:6}, children: (listQ.data||[]).map(w=> jsxs('div',{style:{display:'flex', gap:8, alignItems:'center', padding:'8px 10px', border:'1px solid var(--ui-stroke-secondary)', borderRadius:6, background:'var(--ui-bg-tertiary)'}, children:[
      jsx('span',{style:{fontSize:'.7rem', fontWeight:700, color:'var(--ui-accent)', textTransform:'uppercase'}, children:w.platform}),
      jsx('span',{style:{flex:1, fontSize:'.85rem'}, children:w.query}),
      jsx('button',{onClick:()=>del.mutate(w.id), style:{padding:'4px 8px', border:'1px solid var(--ui-stroke-secondary)', background:'none', borderRadius:6, cursor:'pointer'}, children:'Delete'}),
    ]}, w.id))})
  ]})
}

function PostPane(){
  const [text, setText] = useState('')
  const [status, setStatus] = useState(null) // null | 'posting' | 'success' | 'error'
  const [msg, setMsg] = useState('')
  const post = useMutation({
    mutationFn: ()=> rest('/post', {method:'POST', body:{platform:'x', text}}),
    onMutate: ()=> { setStatus('posting'); setMsg('Posting…') },
    onSuccess: (r)=> {
      setStatus('success')
      setMsg('Posted on X!')
      setText('')
      host.notify({kind:'info', message: 'Posted on X via browser'})
    },
    onError: (e)=> { setStatus('error'); setMsg(String(e.message||e).slice(0,300)); host.notify({kind:'error', message: String(e.message||e).slice(0,300)}) }
  })
  const charCount = text.length
  const overLimit = charCount > 2800
  return jsxs('div',{style:{display:'flex', flexDirection:'column', gap:12, padding:16}, children:[
    jsx('div',{style:{fontWeight:600, fontSize:'.9rem'}, children:'Post on X (browser — no API key)'}),
    jsx('div',{className:`${ID}-hint`, children:'Uses persistent browser context. Make sure you are logged in via Settings → Auth.'}),
    jsx('textarea',{
      value: text,
      onChange: e=> setText(e.target.value),
      placeholder:'What is happening? (max 2800 chars)',
      rows: 5,
      maxLength: 3000,
      style:{ width:'100%', padding:'10px', border:'1px solid var(--ui-stroke-secondary)', borderRadius:6, background:'var(--ui-bg-elevated)', color:'var(--ui-text-primary)', fontSize:'.9rem', resize:'vertical', fontFamily:'inherit'}
    }),
    jsxs('div',{style:{display:'flex', justifyContent:'space-between', alignItems:'center'}, children:[
      jsx('span',{style:{fontSize:'.7rem', color: overLimit ? 'var(--ui-danger, #c33)' : 'var(--ui-text-tertiary)'}, children: `${charCount}/2800`}),
      jsx('button',{
        onClick:()=> { if(text.trim() && !overLimit) post.mutate() },
        disabled: post.isPending || !text.trim() || overLimit,
        style:{padding:'8px 16px', background:'var(--ui-accent)', color:'#fff', border:0, borderRadius:6, cursor:'pointer'}
      }, post.isPending?'Posting…':'Post on X'),
    ]}),
    status==='success' ? jsx('div',{style:{fontSize:'.8rem', color:'var(--ui-success, #2d7)', padding:'6px 10px', border:'1px solid var(--ui-success, #2d7)', borderRadius:6, background:'var(--ui-bg-tertiary)'}, children: msg}) : null,
    status==='error' ? jsx('div',{style:{fontSize:'.8rem', color:'var(--ui-danger, #c33)', padding:'6px 10px', border:'1px solid var(--ui-danger, #c33)', borderRadius:6, background:'var(--ui-bg-tertiary)'}, children: msg}) : null,
  ]})
}

function LatestPane({ query, platform }){
  const localQ = useQuery({
    queryKey: [ID,'posts', platform, query],
    queryFn: async () => {
      const p = new URLSearchParams({platform, limit:'30'})
      if(query) p.set('query', query)
      const r = await rest(`/posts?${p.toString()}`)
      return r
    },
    refetchInterval: 60000,
  })
  const [remote,setRemote] = useState(null)
  useEffect(()=>{
    let cancelled=false
    try{
      const routes = host.profileRoutes?.() || []
      const er = routes.find(r => (r.targetProfile||r.profile||'').toLowerCase().includes('euphoria'))
      if(!er) return
      host.requestProfile(er, 'social.list', {platform, query, limit:20}).then(res=>{
        if(!cancelled) setRemote(res)
      }).catch(()=>{})
    }catch{}
    return ()=>{cancelled=true}
  }, [platform, query])
  const refresh = useMutation({
    mutationFn: ()=> rest('/refresh-all', {method:'POST', body:{}}),
    onSuccess: ()=> queryClient.invalidateQueries({queryKey:[ID,'posts']})
  })
  const doSearch = useMutation({
    mutationFn: ()=> rest('/search', {method:'POST', body:{platform, query: query || 'AI design', limit:12}}),
    onSuccess: ()=> queryClient.invalidateQueries({queryKey:[ID,'posts']})
  })
  const posts = [...(localQ.data?.posts||[]), ...(remote?.posts||[])]
    .sort((a,b)=> Date.parse(b.ts||0)-Date.parse(a.ts||0))
  return jsxs('div',{children:[
    jsxs('div',{style:{display:'flex', gap:6, padding:'8px 16px', justifyContent:'flex-end'}, children:[
      jsx('button',{onClick:()=>doSearch.mutate(), style:{padding:'4px 8px', border:'1px solid var(--ui-accent)', borderRadius:4, background:'none', color:'var(--ui-accent)', cursor:'pointer'}, children: doSearch.isPending?'Searching…':'Search'}),
      jsx('button',{onClick:()=>refresh.mutate(), style:{padding:'4px 8px', background:'var(--ui-accent)', color:'#fff', border:0, borderRadius:4, cursor:'pointer'}, children: refresh.isPending?'Refreshing…':'Refresh All'}),
    ]}),
    localQ.isLoading ? jsx('div',{style:{padding:16, color:'var(--ui-text-tertiary)'}, children:'Loading…'}) :
    posts.length===0 ? jsx('div',{style:{padding:24, textAlign:'center', color:'var(--ui-text-tertiary)'}, children:'No posts yet — add a Watchlist query or hit Search. Local browser needs one-time login via Settings → Auth for X/LinkedIn.'}) :
    jsx('div',{className:`${ID}-list`, children: posts.slice(0,40).map(p=> jsx('div',{className:`${ID}-row`, children:[
      jsx('div',{style:{flex:1, minWidth:0}, children:[
        jsxs('div',{className:`${ID}-meta`, children:[
          jsx('span',{className:`${ID}-src`, children: p.platform}),
          jsx('span',{children:`· ${p.author||'unknown'}`}),
          jsx('span',{children:`· ${timeAgo(p.ts)}`}),
        ]}),
        jsx('div',{style:{fontSize:'.85rem', lineHeight:1.4, color:'var(--ui-text-primary)', whiteSpace:'pre-wrap'}, children: (p.text||'').slice(0,600)}),
        p.url ? jsx('a',{href:p.url, target:'_blank', rel:'noreferrer', style:{fontSize:'.7rem', color:'var(--ui-accent)'}, children: p.url.slice(0,80)}):null,
      ]}),
    ]}, p.id))})
  ]})
}

function SocialPage(){
  ensureStyles()
  const [tab,setTab] = useState(() => storageGet?.('lastTab','latest') || 'latest')
  const [query,setQuery] = useState('')
  const [platform,setPlatform] = useState(() => storageGet?.('platform','x') || 'x')
  useEffect(()=> storageSet?.('lastTab', tab), [tab])
  useEffect(()=> storageSet?.('platform', platform), [platform])
  return jsxs('div',{className:`${ID}-page`, children:[
    jsxs('div',{className:`${ID}-tabs`, children:[
      jsx('button',{className:`${ID}-tab`, 'data-active': tab==='post'?'1':'0', onClick:()=>setTab('post'), children:'Post'}),
      jsx('button',{className:`${ID}-tab`, 'data-active': tab==='latest'?'1':'0', onClick:()=>setTab('latest'), children:'Latest'}),
      jsx('button',{className:`${ID}-tab`, 'data-active': tab==='watchlist'?'1':'0', onClick:()=>setTab('watchlist'), children:'Watchlist'}),
      jsx('button',{className:`${ID}-tab`, 'data-active': tab==='settings'?'1':'0', onClick:()=>setTab('settings'), children:'Settings'}),
      jsx('span',{style:{marginLeft:'auto', display:'flex', gap:'.4rem', alignItems:'center'}, children:[
        tab==='latest' ? jsx('select',{value:platform, onChange:e=>setPlatform(e.target.value), style:{background:'var(--ui-bg-elevated)', color:'var(--ui-text-primary)', border:'1px solid var(--ui-stroke-secondary)', borderRadius:4, padding:'2px 6px'}, children:[
          jsx('option',{value:'x', children:'X'}),
          jsx('option',{value:'linkedin', children:'LinkedIn'}),
          jsx('option',{value:'instagram', children:'Instagram'}),
          jsx('option',{value:'youtube', children:'YouTube'}),
        ]}) : null,
      ]}),
    ]}),
    tab==='post' ? jsx(PostPane,{}) : null,
    tab==='latest' ? jsxs('div',{children:[
      jsx('div',{style:{display:'flex', gap:8, padding:'8px 16px', borderBottom:'1px solid var(--ui-stroke-tertiary)'}, children:[
        jsx('input',{placeholder:'Search query (e.g. AI automation)', value:query, onChange:e=>setQuery(e.target.value), style:{flex:1, background:'var(--ui-bg-tertiary)', border:'1px solid var(--ui-stroke-secondary)', borderRadius:6, padding:'6px 10px', color:'var(--ui-text-primary)'}}),
      ]}),
      jsx(LatestPane,{query, platform})
    ]}) : null,
    tab==='watchlist' ? jsx(WatchlistPane,{}) : null,
    tab==='settings' ? jsx(SettingsPane,{}) : null,
  ]})
}

export default {
  id: ID,
  name: 'Social Research',
  register(ctx){
    ensureStyles()
    rest = ctx.rest
    storageGet = (k,fb)=> ctx.storage.get(k,fb)
    storageSet = (k,v)=> ctx.storage.set(k,v)
    osOpen = ctx.os?.openExternal ? (url)=> ctx.os.openExternal(url) : null
    ctx.register({id:'page', area: ROUTES_AREA, data:{path:'/social'}, render: ()=> jsx(SocialPage,{})})
    ctx.register({id:'nav', area: SIDEBAR_NAV_AREA, data:{path:'/social', label:'Social Research', codicon:'globe'}})
    ctx.register({id:'palette-search', area: PALETTE_AREA, data:{id:'social-bridge.search', label:'Social: Search', keywords:['social','x','linkedin'], run:()=> host.navigate('/social')}})
    ctx.register({id:'palette-refresh', area: PALETTE_AREA, data:{id:'social-bridge.refresh', label:'Social: Refresh All', keywords:['refresh','social'], run:()=> void rest('/refresh-all',{method:'POST', body:{}})}})
    void rest('/settings').catch(()=>{})
  }
}
