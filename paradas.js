/**
 * API TUSSAM - Mapa de paradas
 * ============================
 *
 * Página estática que pinta las 967 paradas de TUSSAM sobre un mapa Leaflet y
 * permite buscarlas por nombre o calle y filtrarlas por línea o por zona
 * (código postal). Los datos provienen de dos instantáneas JSON generadas desde
 * la base de datos de la API (paradas.json y lineas.json); no hay backend.
 *
 * El mapa y la lista lateral están sincronizados: filtrar actualiza ambos, y
 * pulsar una parada en la lista la centra en el mapa y abre su ficha.
 */

(function () {
  "use strict";

  // Centro aproximado de Sevilla y límite de resultados renderizados en la lista
  // (el mapa siempre muestra todos los que pasan el filtro; la lista se acota
  // para no crear cientos de nodos del DOM en cada pulsación de tecla).
  const SEVILLA = [37.3826, -5.9756];
  const MAX_LISTA = 300;

  const estado = {
    paradas: [],
    colorPorLinea: new Map(),
    nombrePorLinea: new Map(),
    marcadores: new Map(), // codigo -> L.CircleMarker
    grupo: null, // featureGroup con los marcadores visibles
    filtroLinea: null,
    filtroZona: "",
    consulta: "",
  };

  let mapa;

  /** Escapa caracteres HTML antes de insertarlos con innerHTML.
   *
   * Los datos vienen de nuestro snapshot JSON, pero los nombres y calles proceden
   * en origen de TUSSAM; escaparlos evita que un carácter especial se interprete
   * como marcado (defensa en profundidad frente a XSS).
   */
  function esc(valor) {
    return String(valor).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[c]);
  }

  /** Normaliza texto para búsquedas insensibles a mayúsculas y acentos. */
  function normalizar(texto) {
    return (texto || "")
      .toString()
      .normalize("NFD")
      .replace(/\p{Diacritic}/gu, "")
      .toLowerCase();
  }

  /** Devuelve el color de una parada: el de su primera línea, o el naranja TUSSAM. */
  function colorParada(parada) {
    for (const linea of parada.lineas) {
      const color = estado.colorPorLinea.get(linea);
      if (color) return color;
    }
    return "#f7a800";
  }

  /** Construye el HTML de los chips de línea de una parada. */
  function chipsLineas(parada) {
    return parada.lineas
      .map((linea) => {
        const color = estado.colorPorLinea.get(linea) || "#141414";
        return `<span class="mini-line" style="--c:${esc(color)}">${esc(linea)}</span>`;
      })
      .join("");
  }

  /** Contenido de la ficha (popup) de una parada. */
  function fichaPopup(parada) {
    const dir = [parada.calle, parada.numero].filter(Boolean).join(" ");
    const zona = parada.cp ? ` · ${esc(parada.cp)}` : "";
    return `
      <div class="popup-stop">
        <div class="popup-head"><span class="popup-code">${esc(parada.codigo)}</span>
          <strong>${esc(parada.nombre)}</strong></div>
        ${dir ? `<p class="popup-dir">${esc(dir)}${zona}</p>` : ""}
        <div class="popup-lines">${chipsLineas(parada)}</div>
        <code class="popup-endpoint">GET /paradas/${esc(parada.codigo)}/tiempos</code>
      </div>`;
  }

  /** ¿Pasa la parada los filtros activos (línea, zona, búsqueda)? */
  function coincide(parada) {
    if (estado.filtroLinea && !parada.lineas.includes(estado.filtroLinea)) {
      return false;
    }
    if (estado.filtroZona && parada.cp !== estado.filtroZona) {
      return false;
    }
    if (estado.consulta) {
      const heno = normalizar(parada.nombre + " " + parada.calle);
      if (!heno.includes(estado.consulta)) return false;
    }
    return true;
  }

  /** Aplica los filtros: recalcula marcadores visibles, lista y contador. */
  function aplicarFiltros(ajustarVista) {
    const visibles = estado.paradas.filter(coincide);

    // Marcadores: reutiliza los objetos ya creados, solo cambia cuáles están.
    estado.grupo.clearLayers();
    for (const parada of visibles) {
      estado.grupo.addLayer(estado.marcadores.get(parada.codigo));
    }

    // Contador
    const count = document.getElementById("results-count");
    count.textContent =
      visibles.length === 1
        ? "1 parada"
        : `${visibles.length.toLocaleString("es-ES")} paradas`;

    // Lista (acotada)
    const lista = document.getElementById("stop-list");
    lista.innerHTML = "";
    const fragment = document.createDocumentFragment();
    for (const parada of visibles.slice(0, MAX_LISTA)) {
      const li = document.createElement("li");
      li.className = "stop-item";
      li.dataset.codigo = parada.codigo;
      const dir = [parada.calle, parada.numero].filter(Boolean).join(" ");
      li.innerHTML = `
        <span class="stop-item-code">${esc(parada.codigo)}</span>
        <span class="stop-item-body">
          <strong>${esc(parada.nombre)}</strong>
          <small>${esc(dir || "Sin dirección")}${parada.cp ? " · " + esc(parada.cp) : ""}</small>
          <span class="stop-item-lines">${chipsLineas(parada)}</span>
        </span>`;
      fragment.append(li);
    }
    lista.append(fragment);

    if (visibles.length > MAX_LISTA) {
      const aviso = document.createElement("li");
      aviso.className = "stop-item-more";
      aviso.textContent = `y ${(visibles.length - MAX_LISTA).toLocaleString("es-ES")} más en el mapa`;
      lista.append(aviso);
    }

    // Encaja la vista en los resultados solo cuando cambia línea o zona, no en
    // cada tecla de búsqueda (sería mareante).
    if (ajustarVista && visibles.length) {
      const bounds = estado.grupo.getBounds();
      if (bounds.isValid()) mapa.fitBounds(bounds.pad(0.15));
    }
  }

  /** Centra el mapa en una parada y abre su ficha. */
  function irAParada(codigo) {
    const marcador = estado.marcadores.get(codigo);
    if (!marcador) return;
    mapa.flyTo(marcador.getLatLng(), Math.max(mapa.getZoom(), 16), {
      duration: 0.6,
    });
    marcador.openPopup();
  }

  /** Rellena los chips de línea a partir de lineas.json. */
  function pintarChipsLinea(lineas) {
    const cont = document.getElementById("line-chips");
    for (const linea of lineas) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "line-chip";
      chip.style.setProperty("--c", linea.color);
      chip.dataset.linea = linea.numero;
      chip.textContent = linea.numero;
      chip.title = `${linea.numero} · ${linea.nombre}`;
      chip.setAttribute("aria-pressed", "false");
      cont.append(chip);
    }
  }

  /** Rellena el desplegable de zonas con los códigos postales presentes. */
  function pintarZonas(paradas) {
    const select = document.getElementById("zone-select");
    const cps = [...new Set(paradas.map((p) => p.cp).filter(Boolean))].sort();
    for (const cp of cps) {
      const opt = document.createElement("option");
      opt.value = cp;
      opt.textContent = cp;
      select.append(opt);
    }
  }

  /** Marca visualmente el chip de línea activo. */
  function actualizarChipsActivos() {
    document.querySelectorAll(".line-chip").forEach((chip) => {
      const activo = chip.dataset.linea === estado.filtroLinea;
      chip.classList.toggle("is-active", activo);
      chip.setAttribute("aria-pressed", String(activo));
    });
  }

  /** Conecta los eventos de búsqueda, chips, zona y lista. */
  function conectarEventos() {
    // Búsqueda con pequeño retardo para no filtrar en cada pulsación.
    let debounce;
    document.getElementById("stop-search").addEventListener("input", (e) => {
      window.clearTimeout(debounce);
      const valor = normalizar(e.target.value.trim());
      debounce = window.setTimeout(() => {
        estado.consulta = valor;
        aplicarFiltros(false);
      }, 140);
    });

    // Chips de línea (toggle).
    document.getElementById("line-chips").addEventListener("click", (e) => {
      const chip = e.target.closest(".line-chip");
      if (!chip) return;
      const linea = chip.dataset.linea;
      estado.filtroLinea = estado.filtroLinea === linea ? null : linea;
      actualizarChipsActivos();
      aplicarFiltros(true);
    });

    // Zona.
    document.getElementById("zone-select").addEventListener("change", (e) => {
      estado.filtroZona = e.target.value;
      aplicarFiltros(true);
    });

    // "Todas": reinicia todos los filtros.
    document.getElementById("reset-filters").addEventListener("click", () => {
      estado.filtroLinea = null;
      estado.filtroZona = "";
      estado.consulta = "";
      document.getElementById("stop-search").value = "";
      document.getElementById("zone-select").value = "";
      actualizarChipsActivos();
      aplicarFiltros(true);
    });

    // Lista: pulsar una parada la centra en el mapa.
    document.getElementById("stop-list").addEventListener("click", (e) => {
      const item = e.target.closest(".stop-item");
      if (item) irAParada(item.dataset.codigo);
    });
  }

  /** Arranca el mapa y toda la interacción una vez cargados los datos. */
  function iniciar(paradas, lineas) {
    estado.paradas = paradas;
    for (const linea of lineas) {
      estado.colorPorLinea.set(linea.numero, linea.color);
      estado.nombrePorLinea.set(linea.numero, linea.nombre);
    }

    mapa = L.map("map", {
      center: SEVILLA,
      zoom: 12,
      preferCanvas: true, // renderiza los ~967 marcadores en canvas (más fluido)
    });

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(mapa);

    estado.grupo = L.featureGroup().addTo(mapa);

    // Crear un marcador por parada (una sola vez).
    for (const parada of paradas) {
      const marcador = L.circleMarker([parada.lat, parada.lon], {
        radius: 5,
        color: "#141414",
        weight: 1,
        fillColor: colorParada(parada),
        fillOpacity: 0.9,
      });
      marcador.bindPopup(fichaPopup(parada));
      estado.marcadores.set(parada.codigo, marcador);
    }

    pintarChipsLinea(lineas);
    pintarZonas(paradas);
    conectarEventos();
    aplicarFiltros(true);
  }

  /** Muestra un error legible si algo falla al cargar los datos. */
  function mostrarError(mensaje) {
    const count = document.getElementById("results-count");
    if (count) count.textContent = mensaje;
  }

  // Carga de datos y arranque.
  Promise.all([
    fetch("paradas.json").then((r) => {
      if (!r.ok) throw new Error("paradas.json " + r.status);
      return r.json();
    }),
    fetch("lineas.json").then((r) => {
      if (!r.ok) throw new Error("lineas.json " + r.status);
      return r.json();
    }),
  ])
    .then(([paradas, lineas]) => iniciar(paradas, lineas))
    .catch((err) => {
      mostrarError("No se pudieron cargar los datos del mapa.");
      // eslint-disable-next-line no-console
      console.error("Error cargando datos del mapa:", err);
    });
})();
