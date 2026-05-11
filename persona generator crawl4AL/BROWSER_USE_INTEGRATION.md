# 🌐 Intégration Browser Use — Documentation Complète

## Vue d'ensemble

Browser Use est intégré au projet pour **lancer un navigateur isolé (sandbox) et capturer des screenshots visionnés par l'IA**. L'intégration fonctionne en 3 phases:

1. **Initialisation** — créer les flags et structures de données
2. **Lancement du Sandbox** — démarrer Browser Use et obtenir l'URL CDP
3. **Capture de Screenshots** — à chaque étape ReAct, capturer des images pour l'IA

---

## Phase 1 : Initialisation

### Fichiers et Imports

**Localisation**: [Backend/src/agents/persona_agent.py](Backend/src/agents/persona_agent.py#L35-L48)

```python
# BROWSER USE INTEGRATION — START
try:
    from browser_use import Browser
    from browser_use.browser.config import BrowserConfig
    BROWSER_USE_AVAILABLE = True
except ImportError:
    BROWSER_USE_AVAILABLE = False
    # Fallback for newer browser-use versions where BrowserConfig moved.
    try:
        from browser_use import Browser, BrowserProfile as BrowserConfig
        BROWSER_USE_AVAILABLE = True
    except ImportError:
        pass
# BROWSER USE INTEGRATION — END
```

**Ce qui se passe**:
- Essaie d'importer `Browser` et `BrowserConfig` depuis `browser_use`
- Si l'import échoue, essaie de fallback vers `BrowserProfile` (version plus récente)
- Définit un flag `BROWSER_USE_AVAILABLE` (True/False) pour savoir si Browser Use est disponible

### Configuration dans `__init__`

**Localisation**: [Backend/src/agents/persona_agent.py#L87-L124](Backend/src/agents/persona_agent.py#L87-L124)

```python
# BROWSER USE INTEGRATION — START
self._last_screenshot: Optional[str] = None

# Determine which API key to check based on vision provider
_vision_provider = self.config.vision_provider
if _vision_provider == "google":
    _vision_key_available = bool(os.getenv("GOOGLE_API_KEY"))
    _vision_key_name = "GOOGLE_API_KEY"
    _vision_model_display = f"gemini ({self.config.vision_model})"
# ... autres providers ...

self._attach_observation_images = bool(
    self.config.vision_enabled        # Vision doit être activée
    and self.config.sandbox           # Sandbox (Browser Use) doit être activé
    and _vision_key_available         # API key du provider doit être présent
)
# BROWSER USE INTEGRATION — END
```

**Structures créées**:
- `self._last_screenshot` — stocke la dernière capture en base64
- `self._attach_observation_images` — flag qui détermine si les images sont jointes au LLM
  - = `True` si: `vision_enabled=true` ET `sandbox=true` ET clé API présente
  - = `False` sinon

---

## Phase 2 : Lancement du Sandbox

### Fonction `_launch_sandbox()`

**Localisation**: [Backend/src/agents/persona_agent.py#L516-L545](Backend/src/agents/persona_agent.py#L516-L545)

```python
async def _launch_sandbox(self):
    """Launch Browser Use isolated session and expose CDP endpoint."""
    if not self.config.sandbox or not BROWSER_USE_AVAILABLE:
        return None, None  # ← Si sandbox désactivé ou Browser Use non disponible

    try:
        # Étape 1: Créer la config du navigateur
        browser_cfg = BrowserConfig(headless=self.config.headless)
        
        # Étape 2: Créer l'instance Browser Use
        try:
            browser = Browser(config=browser_cfg)          # Version ancienne
        except TypeError:
            browser = Browser(browser_profile=browser_cfg) # Version récente
        
        # Étape 3: Démarrer le navigateur
        start_result = await browser.start()
        
        # Étape 4: Récupérer l'URL CDP
        cdp_url = getattr(browser, "cdp_url", None)
        if not cdp_url and isinstance(start_result, dict):
            cdp_url = start_result.get("cdp_url")
        
        # Étape 5: Vérifier qu'on a bien une URL CDP
        if not cdp_url:
            print("⚠ Browser Use sandbox started but no CDP endpoint was found. Falling back.")
            try:
                await browser.stop()
            except Exception:
                pass
            return None, None
        
        return browser, cdp_url  # ← Retour du navigateur et de l'URL CDP
    except Exception as e:
        print(f"⚠ Browser Use sandbox launch failed: {e}. Falling back.")
        return None, None
```

**Inputs**:
- `self.config.sandbox` — doit être `True` pour lancer (vérifier config.yaml)
- `self.config.headless` — passe au `BrowserConfig` pour démarrage headless ou non

**Output**:
- `(browser, cdp_url)` — tuple avec:
  - `browser` : instance Browser Use active
  - `cdp_url` : URL du Chrome DevTools Protocol (ex: `http://localhost:9222`)
- `(None, None)` si le sandbox échoue

### Appel du Sandbox dans `run_with_mcp()`

**Localisation**: [Backend/src/agents/persona_agent.py#L615-L625](Backend/src/agents/persona_agent.py#L615-L625)

```python
# BROWSER USE INTEGRATION — START
bu_browser, cdp_url = await self._launch_sandbox()

async def _close_sandbox_if_needed() -> None:
    if bu_browser is not None:
        try:
            if hasattr(bu_browser, "close"):
                await bu_browser.close()
            elif hasattr(bu_browser, "stop"):
                await bu_browser.stop()
        except Exception:
            pass
# BROWSER USE INTEGRATION — END
```

### Passage de l'URL CDP à Playwright MCP

**Localisation**: [Backend/src/agents/persona_agent.py#L655-L665](Backend/src/agents/persona_agent.py#L655-L665)

```python
# ── Connect via stdio (keeps session alive) ────────────────
print("🔌 Connecting to Playwright MCP Server...")
# BROWSER USE INTEGRATION — START
mcp_args = ["@playwright/mcp", "--cdp-endpoint", cdp_url] if cdp_url is not None else ["@playwright/mcp"] + headless_args
# BROWSER USE INTEGRATION — END
server_params = StdioServerParameters(
    command="npx",
    args=mcp_args,  # ← Passe l'URL CDP ici
)
```

**Ce qui se passe**:
- Si `cdp_url` est présent → args = `["@playwright/mcp", "--cdp-endpoint", "http://localhost:9222"]`
- Si `cdp_url` est None → args = `["@playwright/mcp", "--headless"]`
- Playwright MCP se connecte au navigateur Browser Use via le CDP endpoint

---

## Phase 3 : Capture de Screenshots

### Condition d'activation

**Localisation**: [Backend/src/agents/persona_agent.py#L1465](Backend/src/agents/persona_agent.py#L1465)

```python
_use_observation_images = self._attach_observation_images
```

Cela initialise le flag au début de la boucle ReAct. Le flag peut être désactivé pendant la boucle (ex. si too many requests).

### Fonction `_capture_sandbox_screenshot()`

**Localisation**: [Backend/src/agents/persona_agent.py#L546-L570](Backend/src/agents/persona_agent.py#L546-L570)

```python
async def _capture_sandbox_screenshot(self, browser) -> Optional[str]:
    """Capture screenshot from Browser Use session as base64 string."""
    if browser is None:
        return None
    
    # Méthode 1: Capturer via take_screenshot()
    if hasattr(browser, "take_screenshot"):
        shot = await browser.take_screenshot(full_page=False)
        
        if isinstance(shot, bytes):
            # Encoder les bytes en base64
            return base64.b64encode(shot).decode("utf-8")
        
        if isinstance(shot, str):
            # Si déjà en data URL, extraire la partie base64
            if shot.startswith("data:image"):
                return shot.split(",", 1)[1]
            return shot
    
    # Méthode 2: Fallback via get_browser_state_summary()
    if hasattr(browser, "get_browser_state_summary"):
        state = await browser.get_browser_state_summary(include_screenshot=True)
        screenshot = getattr(state, "screenshot", None)
        if screenshot:
            return screenshot
    
    return None
```

**Inputs**:
- `browser` : instance Browser Use retournée par `_launch_sandbox()`

**Output**:
- String base64 du screenshot (ex: `"iVBORw0KGgoAAAA...=="`)
- `None` si échouée

### Boucle de Capture dans ReAct

**Localisation**: [Backend/src/agents/persona_agent.py#L2614-L2650](Backend/src/agents/persona_agent.py#L2614-L2650)

```python
# BROWSER USE INTEGRATION — START
# Take screenshot on key steps to give LLM visual context
# Only on steps that change page content meaningfully
visual_steps = (
    "browser_navigate",      # ← Changement d'URL
    "browser_snapshot",      # ← Actualisation manuelle
    "browser_click",         # ← Interaction utilisateur
    "browser_type",          # ← Saisie de texte
)

if _use_observation_images and action_name in visual_steps:
    try:
        # Préférence 1: Browser Use (le plus fiable pour les screenshots)
        if bu_browser is not None:
            try:
                self._last_screenshot = await self._capture_sandbox_screenshot(bu_browser)
                if self._last_screenshot:
                    print(f"👁️  Vision: screenshot captured (Browser Use)")
            except Exception as e:
                print(f"⚠ Browser Use screenshot failed: {e}")
                self._last_screenshot = None
        
        # Fallback 2: MCP Playwright si Browser Use non disponible
        elif "browser_take_screenshot" in tools_dict:
            try:
                screenshot_result = await session.call_tool("browser_take_screenshot", {})
                screenshot_str = str(screenshot_result)
                
                # Parser la réponse pour extraire base64
                if "base64," in screenshot_str:
                    self._last_screenshot = screenshot_str.split("base64,")[-1].strip("'\"")
                elif len(screenshot_str) > 100 and not screenshot_str.startswith("["):
                    self._last_screenshot = screenshot_str.strip("'\"")
                
                if self._last_screenshot:
                    print(f"👁️  Vision: screenshot captured (MCP)")
            except Exception as e:
                print(f"⚠ MCP screenshot failed: {e}")
                self._last_screenshot = None
    except Exception as e:
        print(f"⚠ Screenshot capture error: {e}")
        self._last_screenshot = None
else:
    self._last_screenshot = None
# BROWSER USE INTEGRATION — END
```

**Logic**:
1. SI `_use_observation_images` est True ET `action_name` dans `visual_steps`:
   - Essayer de capturer via Browser Use
   - Si échouée → fallback sur MCP Playwright
   - Sinon → pas de screenshot
2. Stocker le base64 dans `self._last_screenshot`

### Envoi au LLM Vision

**Localisation**: [Backend/src/agents/persona_agent.py#L2683-L2700](Backend/src/agents/persona_agent.py#L2683-L2700)

```python
# VISION MODE — START
vision_image_attached = False
if self._last_screenshot and _use_observation_images:
    messages.append(HumanMessage(
        content=[
            {"type": "text", "text": observation_text},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{self._last_screenshot}"
                },
            },
        ]
    ))
    _current_llm = self.vision_llm  # ← Utiliser le LLM vision (ex: Claude, GPT-4o)
    vision_image_attached = True
else:
    messages.append(HumanMessage(content=observation_text))
    _current_llm = self.llm  # ← Utiliser le LLM standard
# VISION MODE — END
```

**Ce qui se passe**:
- Si screenshot présent → créer un `HumanMessage` avec TEXTE + IMAGE (format multimodal)
- Passer au LLM vision (`self.vision_llm`) qui peut analyser l'image
- Sinon → usage LLM standard (texte uniquement)

---

## Fermeture du Sandbox

**Localisation**: [Backend/src/agents/persona_agent.py#L620-L625](Backend/src/agents/persona_agent.py#L620-L625)

```python
async def _close_sandbox_if_needed() -> None:
    if bu_browser is not None:
        try:
            if hasattr(bu_browser, "close"):
                await bu_browser.close()
            elif hasattr(bu_browser, "stop"):
                await bu_browser.stop()
        except Exception:
            pass
```

**Appelée à la fin du `run_with_mcp()` pour nettoyer les ressources**:
```python
await _close_sandbox_if_needed()
return {...}  # ← Retourner résultat
```

---

## Configuration Requise (config.yaml)

```yaml
# Activation du Sandbox Browser Use
sandbox: true                    # Default: false
headless: true                   # Headless ou GUI
vision_enabled: true             # Activer vision pour images
vision_provider: "openai"        # ou "google", "github", "ollama"
vision_model: "gpt-4-vision"
```

**Variables d'environnement**:
```bash
# Pour GPT-4 Vision
OPENAI_API_KEY=sk-...

# Pour Gemini Vision
GOOGLE_API_KEY=AIza...

# Pour GitHub Models Vision
GITHUB_TOKEN=github_...
```

---

## Flux Complet (Timeline)

```
┌─────────────────────────────────────────────────────────────────────┐
│ PersonaAgent.__init__()                                             │
├─────────────────────────────────────────────────────────────────────┤
│ 1. Vérifie BROWSER_USE_AVAILABLE (import OK?)                       │
│ 2. Crée self._last_screenshot = None                                │
│ 3. Crée self._attach_observation_images (vision+sandbox+api_key?)   │
└─────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────┐
│ run_with_mcp(start_url)                                             │
├─────────────────────────────────────────────────────────────────────┤
│ 1. await _launch_sandbox()                                          │
│    • BrowserConfig(headless=config.headless)                        │
│    • Browser(config=config)                                         │
│    • await browser.start()                                          │
│    • Récupère cdp_url                                               │
│    • Retourne (browser, cdp_url)                                    │
│                                                                      │
│ 2. Passe cdp_url à Playwright MCP:                                 │
│    mcp_args = ["@playwright/mcp", "--cdp-endpoint", cdp_url]       │
│                                                                      │
│ 3. Démarre la boucle ReAct                                          │
└─────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────┐
│ ReAct Loop: Chaque étape                                            │
├─────────────────────────────────────────────────────────────────────┤
│ 1. LLM retourne ACTION (ex: browser_navigate, browser_click)        │
│ 2. Playwright MCP exécute l'action (LE NAVIGATEUR EST BROWSER USE)  │
│ 3. Si action dans visual_steps ET _use_observation_images:         │
│    • await _capture_sandbox_screenshot(bu_browser)                 │
│    • Encode screenshot en base64                                    │
│    • Stocke dans self._last_screenshot                              │
│ 4. Crée observation_text + screenshot image                         │
│ 5. Envoie au LLM vision (multimodal: texte + image)                 │
│ 6. LLM retourne la prochaine action (retour à étape 1)             │
└─────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────┐
│ Fin du run (max_steps ou DONE)                                      │
├─────────────────────────────────────────────────────────────────────┤
│ 1. await _close_sandbox_if_needed()                                 │
│    • await browser.stop() ou browser.close()                        │
│ 2. Retourner les résultats                                          │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Inputs et Outputs Résumés

### `_launch_sandbox()` Inputs
- `self.config.sandbox` (bool) — doit être True
- `self.config.headless` (bool) — True=headless, False=GUI

### `_launch_sandbox()` Outputs
- `(Browser, str)` — `(browser_instance, "http://localhost:9222")`
- `(None, None)` — si erreur

### `_capture_sandbox_screenshot(browser)` Inputs
- `browser` : Browser Use instance

### `_capture_sandbox_screenshot(browser)` Outputs
- `str` — base64 du screenshot
- `None` — si échouée

### Points d'Appel
1. **Init**: `__init__()` — crée les flags
2. **Sandbox Launch**: `run_with_mcp()` début de boucle
3. **Screenshot**: `run_with_mcp()` après chaque action ReAct
4. **Cleanup**: `run_with_mcp()` fin (retour ou exception)

---

## Troubleshooting

| Problème | Cause | Solution |
|----------|-------|----------|
| Screenshots non capturés | `sandbox: false` ou `vision_enabled: false` | Activer dans config.yaml |
| "Browser Use sandbox started but no CDP endpoint" | Navigateur n'expose pas le CDP | Vérifier version browser_use, relancer |
| "Browser Use not available" | Import échouée | `pip install browser-use` |
| Modal obscurcit l'image vision | Browser Use capture ce qu'il voit | Fermer le modal avant cap screenshot |
| Trop de tokens LLM | Images hautes résolution | Utiliser `full_page=False` (déjà fait) |

