# Browser Use Integration — Diagrammes Visuels

## 1. Architecture Globale

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                          PersonaAgent (Python)                               │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                               │
│  ┌─────────────────┐         ┌──────────────────┐       ┌─────────────────┐ │
│  │ Browser Use     │         │ Playwright MCP   │       │ LLM Vision      │ │
│  │ (Navigateur)    │◄────────►│ (Outils/Tools)   │◄─────►│ (Claude/GPT)    │ │
│  │                 │         │                  │       │                 │ │
│  │ • Chrome        │         │ • click          │       │ • Multimodal    │ │
│  │ • Firefox       │         │ • type           │       │ • Analyse img   │ │
│  │ • Webkit        │         │ • navigate       │       │ • Décisions IA  │ │
│  │                 │         │ • snapshot       │       │                 │ │
│  └─────────────────┘         └──────────────────┘       └─────────────────┘ │
│         ▲                              ▲                        ▲             │
│         │                              │                        │             │
│         └──────────CDP────────────────  ────────Observation─────┘             │
│            (localhost:9222)       +Screenshot                                │
│                                                                               │
└──────────────────────────────────────────────────────────────────────────────┘
```

## 2. Séquence d'Initialisation

```
START: PersonaAgent.__init__(user, scenario, config)
│
├─► Import Browser Use
│   ├─ TRY: from browser_use import Browser, BrowserConfig
│   │   └─► BROWSER_USE_AVAILABLE = True
│   └─ EXCEPT: 
│       ├─ TRY: from browser_use import Browser, BrowserProfile
│       │   └─► BROWSER_USE_AVAILABLE = True
│       └─ EXCEPT:
│           └─► BROWSER_USE_AVAILABLE = False
│
├─► Initialiser LLM (self.llm, self.vision_llm)
│
├─► Initialiser flags Vision/Screenshot:
│   ├─ self._last_screenshot = None
│   ├─ Déterminer vision provider (Google/OpenAI/GitHub/Ollama)
│   ├─ Déterminer _vision_key_available (API key présente?)
│   └─ self._attach_observation_images = (
│       vision_enabled AND sandbox AND api_key_available
│       )
│
END
```

## 3. Séquence de Lancement (run_with_mcp)

```
START: run_with_mcp(start_url)
│
├─► LAUNCH SANDBOX
│   │
│   ├─ Condition: self.config.sandbox = True?
│   │   └─ NON: return (None, None) → pas d'images
│   │   └─ OUI: continuer
│   │
│   ├─ BrowserConfig(headless=config.headless)
│   │   └─ Crée: BrowserConfig instance
│   │
│   ├─ Browser(config=BrowserConfig)
│   │   └─ Crée: Browser instance (Browser Use)
│   │
│   ├─ await browser.start()
│   │   ├─ Lance: Processus Chrome/Firefox/Webkit
│   │   └─ Ouvre: Port CDP (par défaut 9222)
│   │
│   ├─ Récupère: cdp_url = browser.cdp_url
│   │   └─ Exemple: "http://localhost:9222"
│   │
│   └─ return (browser_instance, "http://localhost:9222")
│
├─► CONFIGURE PLAYWRIGHT MCP
│   │
│   ├─ Si cdp_url non-None:
│   │   mcp_args = ["@playwright/mcp", "--cdp-endpoint", cdp_url]
│   │   └─ Argument: "--cdp-endpoint" + PORT CDP
│   │
│   └─ Si cdp_url = None:
│       mcp_args = ["@playwright/mcp", "--headless"]
│       └─ Argument: "--headless" (pas de Browser Use)
│
├─► CONNECT PLAYWRIGHT MCP
│   │
│   ├─ StdioServerParameters(command="npx", args=mcp_args)
│   ├─ stdio_client(server_params)
│   ├─ ClientSession(read, write)
│   ├─ await session.initialize()
│   │
│   ├─ load_mcp_tools(session)
│   │   ├─ Retourne: [browser_navigate, browser_click, ...]
│   │   └─ Stocke en: tools_dict = {name: tool}
│   │
│   └─ Affiche: "✓ Connected! {len(tools_dict)} tools available"
│
└─► LAUNCH REACT LOOP
    └─ while step < max_steps: ...
```

## 4. Boucle ReAct avec Screenshots

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          REACT LOOP - EACH STEP                         │
└─────────────────────────────────────────────────────────────────────────┘

STEP n:
│
├─► LLM.invoke(messages) → "ACTION: browser_navigate\nACTION_INPUT: {...}"
│
├─► PARSE ACTION
│   └─ action_name = "browser_navigate"
│   └─ action_input = {"url": "https://..."}
│
├─► EXECUTE TOOL (Playwright MCP → Browser Use)
│   │
│   └─ await tools_dict[action_name].ainvoke(action_input)
│       │
│       ├─ Utilisé par Browser Use (CDP)
│       ├─ Exécute: navigate URL, click, type, etc.
│       └─ Retourne: résultat, snapshot, erreur
│
├─► CAPTURE SCREENSHOT (si applicable)
│   │
│   ├─ visual_steps = ("browser_navigate", "browser_click", ...)
│   │
│   ├─ Condition: _use_observation_images AND action_name in visual_steps?
│   │   │
│   │   ├─ OUI:
│   │   │   │
│   │   │   ├─ Préférence 1: Browser Use (si bu_browser non-None)
│   │   │   │   │
│   │   │   │   └─ await _capture_sandbox_screenshot(bu_browser)
│   │   │   │       │
│   │   │   │       ├─ if hasattr(browser, "take_screenshot"):
│   │   │   │       │   │
│   │   │   │       │   ├─ shot = await browser.take_screenshot(full_page=False)
│   │   │   │       │   │   └─ Retourne: bytes ou str (image)
│   │   │   │       │   │
│   │   │   │       │   ├─ if isinstance(shot, bytes):
│   │   │   │       │   │   └─ return base64.b64encode(shot).decode("utf-8")
│   │   │   │       │   │
│   │   │   │       │   └─ if isinstance(shot, str):
│   │   │   │       │       └─ return shot.split(",", 1)[1]  # Extract base64
│   │   │   │       │
│   │   │   │       └─ self._last_screenshot = base64_string
│   │   │   │
│   │   │   ├─ Fallback: Playwright MCP
│   │   │   │   │
│   │   │   │   ├─ screenshot_result = await session.call_tool(
│   │   │   │   │       "browser_take_screenshot", {}
│   │   │   │   │   )
│   │   │   │   │
│   │   │   │   └─ self._last_screenshot = parser(screenshot_result)
│   │   │   │
│   │   │   └─ print("👁️ Vision: screenshot captured (Browser Use)")
│   │   │
│   │   └─ NON:
│   │       └─ self._last_screenshot = None
│   │
│   └─ Condition exception:
│       └─ self._last_screenshot = None (et log erreur)
│
├─► BUILD OBSERVATION
│   │
│   ├─ observation_text = f"OBSERVATION ({action_name}):\n{result_for_llm}\nNext?"
│   │
│   └─ Compression + Nettoyage du résultat
│
├─► ATTACH IMAGE (Vision Multimodal)
│   │
│   ├─ Condition: self._last_screenshot AND _use_observation_images?
│   │   │
│   │   ├─ OUI:
│   │   │   │
│   │   │   ├─ message_content = [
│   │   │   │       {"type": "text", "text": observation_text},
│   │   │   │       {
│   │   │   │           "type": "image_url",
│   │   │   │           "image_url": {
│   │   │   │               "url": f"data:image/png;base64,{self._last_screenshot}"
│   │   │   │           }
│   │   │   │       }
│   │   │   │   ]
│   │   │   │
│   │   │   ├─ messages.append(HumanMessage(content=message_content))
│   │   │   │
│   │   │   └─ _current_llm = self.vision_llm  # LLM Vision (multimodal)
│   │   │
│   │   └─ NON:
│   │       │
│   │       ├─ messages.append(HumanMessage(content=observation_text))
│   │       │
│   │       └─ _current_llm = self.llm  # LLM Standard (texte uniquement)
│   │
│   └─ Retour au début de boucle (STEP n+1)
│
└─ [REPEAT jusqu'à max_steps ou DONE]
```

## 5. Timeline Complète d'une Exécution

```
T0: Démarrage
    ├─ Charger persona (voyageur_impulsif.json)
    ├─ Charger scenario (microsoft_menu_sections.yaml)
    └─ Créer PersonaAgent(user, scenario, config)
        └─ BROWSER_USE_AVAILABLE = True ✓
        └─ self._attach_observation_images = True (si sandbox=true + vision=true)

T1: run_with_mcp(start_url="https://booking.com")
    ├─ await _launch_sandbox()
    │   ├─ BrowserConfig(headless=True)
    │   ├─ Browser(config=BrowserConfig)
    │   ├─ await browser.start()
    │   │   └─ Lance Chrome sur localhost:9222
    │   └─ return (browser, "http://localhost:9222")
    │
    └─ StdioServerParameters avec "--cdp-endpoint", "http://localhost:9222"
       └─ Playwright MCP utilisera Browser Use ✓

T2: ReAct Begin
    └─ _use_observation_images = True

T2.1: STEP 1 - browser_navigate
    ├─ action_name = "browser_navigate"
    ├─ action = {"url": "https://booking.com"}
    ├─ Exécute via Browser Use (via CDP)
    │   └─ Navigation effectuée dans Browser Use
    │
    ├─ CAPTURE SCREENSHOT (visual_step = "browser_navigate")
    │   ├─ await _capture_sandbox_screenshot(bu_browser)
    │   ├─ shot = await browser.take_screenshot(full_page=False)
    │   │   └─ Capture: Booking.com homepage
    │   └─ screenshot_base64 = "iVBORw0KGgoAAAA=..."
    │       └─ self._last_screenshot = base64
    │
    └─ LLM.invoke(messages) avec texte + image (multimodal)
       └─ LLM analyse: page, éléments visibles, destination field visibles?

T2.2: STEP 2 - browser_click
    ├─ action = {"target": "e42"}  # ref pour destination combobox
    ├─ Exécute via Browser Use
    ├─ CAPTURE SCREENSHOT
    │   ├─ shot = await browser.take_screenshot(...)
    │   └─ self._last_screenshot = base64  (nouvelle image)
    │
    └─ LLM.invoke() avec nouvelle image
       └─ LLM analyse: dropdown ouvert? quelles options?

T2.3: STEP 3 - browser_type
    ├─ action = {"target": "e42", "text": "London"}
    ├─ Exécute typing via Browser Use
    ├─ CAPTURE SCREENSHOT
    │   └─ self._last_screenshot = base64  (London tapé?)
    │
    └─ LLM.invoke()

... (continues jusqu'à max_steps ou DONE) ...

T2.N: DONE
    ├─ LLM dit "DONE — booking complete"
    ├─ Fermer Browser Use:
    │   └─ await _close_sandbox_if_needed()
    │       └─ await browser.stop()
    │           └─ Chrome process fermé
    │
    └─ return {"status": "completed", "steps": N, ...}
```

## 6. Flux des Configurations

```
config.yaml (Persona-Tester/config/config.yaml)
    │
    ├─ sandbox: true/false
    │   └─ Lance Browser Use?
    │
    ├─ headless: true/false
    │   └─ Headless ou GUI?
    │
    ├─ vision_enabled: true/false
    │   └─ Capture/envoyer images?
    │
    ├─ vision_provider: "openai"/"google"/"github"/"ollama"
    │   └─ Quel LLM pour analyser images?
    │
    └─ vision_model: "gpt-4-vision"/"gemini-pro-vision"/...
        └─ Quel modèle utiliser?

.env
    │
    ├─ OPENAI_API_KEY=sk-...
    │   └─ Requis si vision_provider="openai"
    │
    ├─ GOOGLE_API_KEY=AIza...
    │   └─ Requis si vision_provider="google"
    │
    ├─ GITHUB_TOKEN=ghp_...
    │   └─ Requis si vision_provider="github"
    │
    └─ (Ollama n'a besoin d'aucune clé si version locale)

PersonaAgent.__init__()
    │
    ├─ self._attach_observation_images = bool(
    │       config.vision_enabled
    │       AND config.sandbox
    │       AND (api_key_present)
    │   )
    │
    └─ Si False → pas d'images au LLM
       Si True → images multimodales au LLM
```

## 7. Points Clés de Décision (Conditionnels)

```
┌─────────────────────────────────────────────────────────────────────┐
│              DECISION TREE: Browser Use Active?                     │
└─────────────────────────────────────────────────────────────────────┘

START
 │
 ├─► BROWSER_USE_AVAILABLE?
 │   ├─ NON → Browser Use NOT imported → (bu_browser=None, cdp_url=None)
 │   └─ OUI → Continuer
 │       │
 │       ├─► config.sandbox == True?
 │       │   ├─ NON → Skip sandbox → (bu_browser=None, cdp_url=None)
 │       │   └─ OUI → Continuer
 │       │       │
 │       │       ├─► browser.start() success?
 │       │       │   ├─ NON → (bu_browser=None, cdp_url=None)
 │       │       │   └─ OUI → (bu_browser=browser_instance, cdp_url="http://...")
 │       │       │       │
 │       │       │       ├─► config.vision_enabled == True?
 │       │       │       │   ├─ NON → Screenshots ignorés
 │       │       │       │   └─ OUI → Continuer
 │       │       │       │       │
 │       │       │       │       ├─► API key présente?
 │       │       │       │       │   ├─ NON → Screenshots ignorés
 │       │       │       │       │   └─ OUI → Screenshots capturés! ✓
 │       │       │       │       │
 │       │       │       │       └─► action_name in visual_steps?
 │       │       │       │           ├─ NON → Pas de screenshot
 │       │       │       │           └─ OUI → Capturer screenshot ✓
 │       │       │       │               └─ Envoyer au LLM vision ✓

END: Browser Use Active / Inactive
```

---

## Résumé des Inputs et Outputs

### Pour `_launch_sandbox()`
```
INPUTS:
  • self.config.sandbox (bool)
  • self.config.headless (bool)
  • BROWSER_USE_AVAILABLE (bool)

OUTPUTS:
  • Success: (Browser instance, "http://localhost:9222")
  • Failure: (None, None)
```

### Pour `_capture_sandbox_screenshot()`
```
INPUTS:
  • browser: Browser Use instance (ou None)

OUTPUTS:
  • Success: "iVBORw0KGgoAAAANSUhEUgAAAA..." (base64)
  • Failure: None
```

### Pour ReAct Loop Screenshot Capture
```
INPUTS:
  • bu_browser: Browser instance
  • action_name: "browser_navigate" | "browser_click" | "browser_type" | ...
  • _use_observation_images: bool

OUTPUTS:
  • self._last_screenshot: base64 string ou None
  • Message multimodal au LLM: {text, image_url}
```

