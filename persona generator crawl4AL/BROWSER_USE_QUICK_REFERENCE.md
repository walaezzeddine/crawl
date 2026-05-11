# Browser Use — Quick Reference

## Fichiers de Documentation
- [BROWSER_USE_INTEGRATION.md](BROWSER_USE_INTEGRATION.md) - Intégration complète et détaillée
- [BROWSER_USE_DIAGRAMS.md](BROWSER_USE_DIAGRAMS.md) - Diagrammes visuels et flowcharts

## Localisation du Code (Backend/src/agents/persona_agent.py)

| Component | Ligne | Fonction |
|-----------|-------|----------|
| Imports | 35-48 | Importe Browser, BrowserConfig, définit BROWSER_USE_AVAILABLE |
| Init | 87-124 | Crée _last_screenshot et _attach_observation_images |
| Launch Sandbox | 516-545 | `_launch_sandbox()` - démarre Browser Use, retourne browser + cdp_url |
| Capture Screenshot | 546-570 | `_capture_sandbox_screenshot()` - capture base64 du screenshot |
| MCP Connection | 615-665 | Passe cdp_url à Playwright MCP via `--cdp-endpoint` |
| ReAct Loop | 2614-2650 | Capture screenshots après actions (visual_steps) |
| Vision Multimodal | 2683-2700 | Envoie image + texte au LLM |
| Cleanup | 620-625 | `_close_sandbox_if_needed()` - ferme Browser Use |

## When is Browser Use Called?

```
1. __init__() — initialise les flags
2. run_with_mcp() — Lance le sandbox (début)
3. Boucle ReAct — Capture screenshots après actions
4. run_with_mcp() — Ferme le sandbox (fin)
```

## What Are The Inputs?

| Fonction | Input | Type | Exemple |
|----------|-------|------|---------|
| `_launch_sandbox()` | self.config.sandbox | bool | True |
| | self.config.headless | bool | True |
| `_capture_sandbox_screenshot()` | browser | object | Browser instance |
| | full_page | bool | False |
| ReAct Loop | action_name | str | "browser_click" |
| | _use_observation_images | bool | True |

## Configuration Minimale

```yaml
# config/config.yaml
sandbox: true                    # Activer Browser Use
headless: true                   # Headless (false = GUI)
vision_enabled: true             # Capturer images
vision_provider: "openai"        # Provider
vision_model: "gpt-4-vision"     # Modèle
```

```bash
# .env
OPENAI_API_KEY=sk-...
```

## Key Decisions in Code

```python
# Décision 1: Browser Use disponible?
if not BROWSER_USE_AVAILABLE:
    return None, None

# Décision 2: Sandbox activé?
if not self.config.sandbox or not BROWSER_USE_AVAILABLE:
    return None, None

# Décision 3: Vision multimodal activée?
if self._attach_observation_images and action_name in visual_steps:
    # Capture screenshot et envoie au LLM
else:
    # Pas d'image
```

## Screenshots Taken On These Actions

```python
visual_steps = (
    "browser_navigate",   # Changement d'URL
    "browser_snapshot",   # Refresh forcé
    "browser_click",      # Utilisateur click
    "browser_type",       # Saisie de texte
)
```

## Fallback Chain

1. **Préférence**: Browser Use (`_capture_sandbox_screenshot()`)
2. **Fallback**: Playwright MCP (`browser_take_screenshot` tool)
3. **Default**: Pas d'image

## Architecture Simplifiée

```
PersonaAgent
  ├─ Browser Use
  │   ├─ Chrome/Firefox/Webkit process
  │   └─ CDP Endpoint (localhost:9222)
  │
  ├─ Playwright MCP
  │   ├─ Se connecte au CDP
  │   └─ Exécute tools (click, type, navigate)
  │
  └─ LLM Vision
      ├─ Reçoit: observation_text + screenshot
      └─ Retourne: prochaine ACTION
```

