/**
 * API TUSSAM - Landing
 * ====================
 *
 * Interactividad de la página de presentación:
 * - Copiar comandos y ejemplos al portapapeles con aviso (toast).
 * - Pestañas de ejemplos de integración (curl, JavaScript, Python, GeoJSON).
 * - Animación del tablón de llegadas con datos de muestra realistas.
 *
 * Sin dependencias externas: todo se resuelve con APIs estándar del navegador.
 */

// ── Ejemplos de código por pestaña ───────────────────────────────────
// El nombre de fichero se muestra en la barra del bloque; el cuerpo es el
// contenido que se copia. Se usan datos reales de la red (Puerta de Carmona).
const CODE_SAMPLES = {
  curl: {
    filename: "terminal",
    body: `# Paradas cercanas con sus tiempos de llegada, en una sola llamada
curl 'http://localhost:8081/cercanas?lat=37.3896&lon=-5.9842&radio=300&max_paradas=2'

# Tiempos de una parada concreta
curl 'http://localhost:8081/paradas/43/tiempos'

# Todas las líneas con su color y horarios
curl 'http://localhost:8081/lineas'`,
  },
  js: {
    filename: "cercanas.js",
    body: `// Buses que llegan a las paradas próximas a una ubicación
const params = new URLSearchParams({
  lat: 37.3896,
  lon: -5.9842,
  radio: 300,
  max_paradas: 3,
});

const res = await fetch(\`http://localhost:8081/cercanas?\${params}\`);
const data = await res.json();

for (const parada of data.paradas) {
  console.log(parada.nombre, '->', parada.tiempos_status);
  for (const t of parada.tiempos) {
    console.log(\`  Línea \${t.linea} a \${t.destino}: \${t.tiempo_minutos} min\`);
  }
}`,
  },
  python: {
    filename: "cercanas.py",
    body: `import httpx

# El endpoint agregado resuelve "¿qué bus me viene?" en una petición
resp = httpx.get(
    "http://localhost:8081/cercanas",
    params={"lat": 37.3896, "lon": -5.9842, "radio": 300, "max_paradas": 3},
)
resp.raise_for_status()

for parada in resp.json()["paradas"]:
    print(parada["nombre"], "->", parada["tiempos_status"])
    for t in parada["tiempos"]:
        print(f"  Línea {t['linea']} a {t['destino']}: {t['tiempo_minutos']} min")`,
  },
  geojson: {
    filename: "mapa.js",
    body: `// formato=geojson devuelve un FeatureCollection listo para Leaflet o MapLibre
const params = new URLSearchParams({
  lat: 37.3896,
  lon: -5.9842,
  radio: 400,
  formato: 'geojson',
});

const res = await fetch(\`http://localhost:8081/cercanas?\${params}\`);
const geojson = await res.json();

// geojson.features[].geometry.coordinates = [lon, lat]
L.geoJSON(geojson).addTo(map);`,
  },
};

// ── Datos de muestra del tablón de llegadas ──────────────────────────
// Estructura equivalente a la que devuelve /cercanas, para ilustrar la
// respuesta sin depender de una API en marcha.
const ARRIVALS = {
  43: [
    { linea: "01", color: "#f54129", destino: "Pol. Norte", min: 2 },
    { linea: "C4", color: "#008431", destino: "Circular", min: 5 },
    { linea: "21", color: "#000d6f", destino: "Heliópolis", min: 9 },
  ],
  44: [
    { linea: "27", color: "#f7a800", destino: "Sevilla Este", min: 3 },
    { linea: "32", color: "#84c6e3", destino: "Ciudad Sanitaria", min: 7 },
  ],
};

// ── Pestañas de código ───────────────────────────────────────────────

const codeSample = document.querySelector("#code-sample");
const codeFilename = document.querySelector("#code-filename");

/**
 * Pinta el ejemplo de la pestaña indicada y actualiza el estado ARIA.
 * @param {string} name Clave dentro de CODE_SAMPLES (curl, js, python, geojson).
 */
function renderCode(name) {
  const sample = CODE_SAMPLES[name];
  if (!sample) return;
  codeSample.textContent = sample.body;
  codeFilename.textContent = sample.filename;
}

/**
 * Enlaza un grupo de pestañas accesibles (rol tablist) a su renderizador.
 * Gestiona clic y navegación con flechas, manteniendo tabindex y aria-selected.
 * @param {string} selector Selector de los botones de pestaña.
 * @param {string} dataKey  Nombre del data-* que identifica cada pestaña.
 * @param {(value: string) => void} render Función que pinta el contenido.
 */
function bindTabs(selector, dataKey, render) {
  const tabs = Array.from(document.querySelectorAll(selector));
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => activate(tab));
    tab.addEventListener("keydown", (event) => {
      const idx = tabs.indexOf(tab);
      if (event.key === "ArrowRight" || event.key === "ArrowDown") {
        event.preventDefault();
        activate(tabs[(idx + 1) % tabs.length]);
      } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
        event.preventDefault();
        activate(tabs[(idx - 1 + tabs.length) % tabs.length]);
      }
    });
  });

  function activate(tab) {
    tabs.forEach((t) => {
      const selected = t === tab;
      t.setAttribute("aria-selected", String(selected));
      t.tabIndex = selected ? 0 : -1;
    });
    tab.focus();
    render(tab.dataset[dataKey]);
  }
}

// ── Tablón de llegadas ───────────────────────────────────────────────

/**
 * Renderiza las llegadas de una parada en su lista, con un pequeño retardo
 * escalonado para dar sensación de tablón que se actualiza.
 * @param {string} codigo Código de la parada (clave de ARRIVALS).
 */
function renderArrivals(codigo) {
  const list = document.querySelector(`#arrivals-${codigo}`);
  if (!list) return;
  const items = ARRIVALS[codigo] || [];
  list.innerHTML = "";
  items.forEach((t, i) => {
    const li = document.createElement("li");
    li.className = "arrival";
    li.style.animationDelay = `${i * 90}ms`;
    li.innerHTML = `
      <span class="arrival-line" style="--line-color: ${t.color}">${t.linea}</span>
      <span class="arrival-dest">${t.destino}</span>
      <span class="arrival-min">${t.min === 0 ? "ya" : `${t.min} min`}</span>
    `;
    list.append(li);
  });
}

// ── Copiar al portapapeles ───────────────────────────────────────────

let toastTimer;

/**
 * Copia texto usando el fallback de selección (execCommand) como red de
 * seguridad para navegadores sin API de portapapeles o sin permiso.
 * @param {string} text Texto a copiar.
 * @returns {boolean} true si execCommand reportó éxito.
 */
function copyWithSelection(text) {
  const input = document.createElement("textarea");
  input.value = text;
  input.setAttribute("readonly", "");
  input.style.position = "fixed";
  input.style.opacity = "0";
  document.body.append(input);
  input.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } finally {
    input.remove();
  }
  return copied;
}

/**
 * Copia texto al portapapeles con aviso visual, tolerante a fallos de la API.
 * @param {string} text Texto a copiar.
 */
async function copyText(text) {
  showToast("Copiando");
  const selectionCopied = copyWithSelection(text);
  try {
    if (!navigator.clipboard?.writeText) throw new Error("Clipboard API no disponible");
    await Promise.race([
      navigator.clipboard.writeText(text),
      new Promise((_, reject) =>
        window.setTimeout(() => reject(new Error("Clipboard timeout")), 1000)
      ),
    ]);
    showToast("Copiado");
  } catch {
    showToast(selectionCopied ? "Copiado" : "No se pudo copiar");
  }
}

/**
 * Muestra un mensaje breve en el toast inferior.
 * @param {string} message Texto a mostrar.
 */
function showToast(message) {
  const toast = document.querySelector("#copy-toast");
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add("is-visible");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => toast.classList.remove("is-visible"), 1400);
}

// ── Inicialización ───────────────────────────────────────────────────

bindTabs("[data-code]", "code", renderCode);

document.querySelectorAll("[data-copy]").forEach((button) => {
  button.addEventListener("click", () => copyText(button.dataset.copy));
});

const copyCodeButton = document.querySelector("#copy-code");
if (copyCodeButton) {
  copyCodeButton.addEventListener("click", () => copyText(codeSample.textContent));
}

renderCode("curl");
Object.keys(ARRIVALS).forEach(renderArrivals);

// Pasos de "Cómo funciona": según bajas, cada paso alcanzado se ilumina en
// naranja. Un IntersectionObserver marca el paso como activo cuando su fila
// cruza la mitad superior del viewport; una vez activo, se queda encendido
// (efecto de progreso). Si el navegador no soporta IntersectionObserver, los
// pasos simplemente no se iluminan al hacer scroll (el contenido sigue visible).
(function activarPasosAlHacerScroll() {
  const pasos = document.querySelectorAll(".flow-rail li");
  if (!pasos.length || typeof IntersectionObserver === "undefined") return;

  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-active");
          observer.unobserve(entry.target); // una vez encendido, no se apaga
        }
      }
    },
    // El paso se activa cuando su parte superior llega al 55% inferior del
    // viewport, es decir, cuando aparece por la parte media al bajar.
    { rootMargin: "0px 0px -45% 0px", threshold: 0 }
  );

  pasos.forEach((paso) => observer.observe(paso));
})();
