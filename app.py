import json
import sqlite3
import os
import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Response, Cookie
from fastapi.responses import HTMLResponse, JSONResponse
import qrcode
import io
import base64
import datetime
import hashlib

app = FastAPI()

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def init_db():
    conn = sqlite3.connect("pos_local.db")
    cursor = conn.cursor()
    
    # Tabla de Mesas
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mesas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            numero_mesa TEXT UNIQUE
        )
    """)
    
    # Tabla de Cuenta por Mesas
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cuenta_mesas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mesa TEXT,
            item TEXT,
            precio REAL,
            cantidad INTEGER DEFAULT 1,
            entregado INTEGER DEFAULT 0,
            timestamp TEXT
        )
    """)
    
    # Tabla de Productos / Carta
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT UNIQUE,
            precio REAL
        )
    """)

    # Tabla de Usuarios para Registro y Login
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT,
            rol TEXT
        )
    """)
    
    # Crear un usuario Administrador por defecto si no existe ninguno
    cursor.execute("SELECT COUNT(*) FROM usuarios")
    if cursor.fetchone()[0] == 0:
        admin_pass = hash_password("admin123")
        cursor.execute("INSERT INTO usuarios (username, password, rol) VALUES (?, ?, ?)", 
                       ("admin", admin_pass, "admin"))

    # Productos por defecto si la carta está vacía
    cursor.execute("SELECT COUNT(*) FROM productos")
    if cursor.fetchone()[0] == 0:
        default_productos = [
            ("Cerveza Club Colombia", 6000),
            ("Hamburguesa Artesanal", 25000),
            ("Limonada de Coco", 9000),
            ("Porción de Papas", 12000)
        ]
        cursor.executemany("INSERT INTO productos (nombre, precio) VALUES (?, ?)", default_productos)
    
    conn.commit()
    conn.close()

init_db()

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>POS Pymes - Sistema Web</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 15px; background: #f0f2f5; color: #333; }
        .container { max-width: 900px; margin: auto; }
        .box { background: white; border: 1px solid #ddd; padding: 20px; margin-bottom: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
        h1, h2 { color: #1a73e8; }
        button { padding: 10px 18px; cursor: pointer; border: none; border-radius: 6px; font-size: 14px; font-weight: bold; color: white; background: #1a73e8; transition: opacity 0.2s; }
        button:active { opacity: 0.8; }
        .btn-waiter { background: #f2994a; width: 100%; margin: 8px 0; padding: 12px; }
        .btn-bill { background: #27ae60; width: 100%; margin: 8px 0; padding: 12px; }
        .btn-clean { background: #2f80ed; width: 100%; margin: 8px 0; padding: 12px; }
        .btn-delete { background: #eb5757; padding: 5px 10px; font-size: 11px; }
        .btn-delete:hover { background: #c53030; }
        .btn-edit { background: #f39c12; padding: 5px 10px; font-size: 11px; margin-right: 3px; }
        .btn-edit:hover { background: #d68910; }
        .btn-atendido { background: #27ae60; padding: 6px 12px; font-size: 12px; margin-left: 10px; }
        .btn-cobrar { background: #e67e22; padding: 8px 15px; font-size: 13px; }
        .btn-logout { background: #c0392b; float: right; padding: 6px 12px; font-size: 12px; }
        #log { background: #1e1e1e; color: #00ff00; padding: 12px; height: 140px; overflow-y: scroll; font-family: monospace; font-size: 13px; border-radius: 6px; text-align: left; }
        input, select { padding: 9px; font-size: 14px; border: 1px solid #ccc; border-radius: 5px; margin-right: 8px; box-sizing: border-box; }
        .grid-mesas { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 15px; margin-top: 15px; }
        .card-mesa { background: #fafafa; border: 1px solid #ccc; padding: 12px; border-radius: 6px; text-align: center; }
        .card-mesa img { width: 120px; height: 120px; border: 3px solid white; box-shadow: 0 0 5px rgba(0,0,0,0.1); }
        .table-data { width: 100%; border-collapse: collapse; margin-top: 10px; }
        .table-data th, .table-data td { border: 1px solid #ddd; padding: 8px; text-align: left; font-size: 13px; }
        .table-data th { background: #f8f9fa; }
        .alerta-item { background: #fff3cd; border-left: 5px solid #ffc107; padding: 10px; margin-bottom: 8px; border-radius: 4px; display: flex; justify-content: space-between; align-items: center; }
        .search-container { position: relative; display: inline-block; width: 100%; }
        .autocomplete-list { position: absolute; border: 1px solid #ccc; border-top: none; z-index: 99; top: 100%; left: 0; right: 0; background-color: #fff; max-height: 160px; overflow-y: auto; border-radius: 0 0 5px 5px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); text-align: left; }
        .autocomplete-item { padding: 9px 12px; cursor: pointer; font-size: 14px; border-bottom: 1px solid #eee; }
        .autocomplete-item:hover { background-color: #e9ecef; }
        .auth-card { max-width: 400px; margin: 40px auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 4px 10px rgba(0,0,0,0.1); text-align: center; }
    </style>
</head>
<body>
    <div class="container" id="app"></div>

    <script>
        let ws;
        const urlParams = new URLSearchParams(window.location.search);
        const mesaId = urlParams.get('mesa');
        const esCamarero = urlParams.get('camarero');
        const appDiv = document.getElementById("app");
        let listaProductosCache = [];
        let cacheCuentasCamarero = [];

        async function iniciarApp() {
            if (mesaId) {
                renderVistaCliente();
            } else {
                const res = await fetch('/api/check-session');
                const sesion = await res.json();
                
                if (!sesion.logged) {
                    renderVistaLogin();
                } else if (sesion.rol === 'camarero' || esCamarero) {
                    renderVistaCamarero();
                } else {
                    renderVistaAdmin();
                }
            }
        }

        function renderVistaLogin() {
            appDiv.innerHTML = `
                <div class="auth-card">
                    <h2>🔐 POS Pymes - Acceso</h2>
                    <p style="color: #666; font-size: 14px; margin-bottom: 20px;">Inicia sesión o regístrate para continuar</p>
                    <div style="display: flex; flex-direction: column; gap: 12px;">
                        <input type="text" id="authUsuario" placeholder="Nombre de usuario" style="width: 100%;">
                        <input type="password" id="authPassword" placeholder="Contraseña" style="width: 100%;">
                        <select id="authRol" style="width: 100%;">
                            <option value="admin">Administrador</option>
                            <option value="camarero">Camarero</option>
                        </select>
                        <button onclick="ejecutarLogin()" style="background: #27ae60; margin-top: 5px;">🔑 Iniciar Sesión</button>
                        <button onclick="ejecutarRegistro()" style="background: #1a73e8;">📝 Registrar Nuevo Usuario</button>
                    </div>
                    <p id="authMsg" style="color: #e74c3c; font-size: 13px; margin-top: 15px;"></p>
                </div>
            `;
        }

        async function ejecutarLogin() {
            const u = document.getElementById("authUsuario").value.trim();
            const p = document.getElementById("authPassword").value.trim();
            if(!u || !p) { document.getElementById("authMsg").innerText = "Completa todos los campos."; return; }
            
            const res = await fetch('/api/login', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({username: u, password: p})
            });
            const data = await res.json();
            if(data.success) {
                window.location.reload();
            } else {
                document.getElementById("authMsg").innerText = data.error || "Credenciales inválidas.";
            }
        }

        async function ejecutarRegistro() {
            const u = document.getElementById("authUsuario").value.trim();
            const p = document.getElementById("authPassword").value.trim();
            const r = document.getElementById("authRol").value;
            if(!u || !p) { document.getElementById("authMsg").innerText = "Completa todos los campos."; return; }

            const res = await fetch('/api/register', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({username: u, password: p, rol: r})
            });
            const data = await res.json();
            if(data.success) {
                document.getElementById("authMsg").style.color = "#27ae60";
                document.getElementById("authMsg").innerText = "¡Registro exitoso! Ya puedes iniciar sesión.";
            } else {
                document.getElementById("authMsg").style.color = "#e74c3c";
                document.getElementById("authMsg").innerText = data.error || "El usuario ya existe.";
            }
        }

        async function cerrarSesion() {
            await fetch('/api/logout', {method: 'POST'});
            window.location.href = '/';
        }

        function renderVistaCliente() {
            appDiv.innerHTML = `
                <div class="box" style="text-align: center;">
                    <h1>Mesa ${mesaId}</h1>
                    <div style="background: #e8f5e9; border: 1px solid #c8e6c9; padding: 10px; border-radius: 6px; margin-bottom: 15px;">
                        <span style="font-size: 14px; color: #2e7d32;">Total Consumido:</span>
                        <h2 id="totalMesaCliente" style="margin: 5px 0 0 0; color: #27ae60;">$0</h2>
                    </div>
                    <p>¿Qué necesitas de nuestro equipo?</p>
                    <button class="btn-waiter" onclick="enviarAccionWS('CALL_WAITER', 'Llamar al camarero')">🙋‍♂️ Llamar Camarero</button>
                    <button class="btn-bill" onclick="enviarAccionWS('ASK_BILL', 'Pedir la cuenta')">💵 Pedir la Cuenta</button>
                    <button class="btn-clean" onclick="enviarAccionWS('CLEAN_TABLE', 'Solicitar limpieza')">🧹 Limpiar Mesa</button>
                    
                    <div style="margin-top: 25px; text-align: left;">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <h3 style="margin: 0; color: #1a73e8;">📋 Tu Comanda Actual</h3>
                            <button onclick="actualizarDatosCliente()" style="background: #6c757d; padding: 5px 10px; font-size: 12px;">🔄 Actualizar</button>
                        </div>
                        <div style="max-height: 250px; overflow-y: auto; margin-top: 8px;">
                            <table class="table-data">
                                <thead>
                                    <tr><th>Cant.</th><th>Ítem</th><th>Subtotal</th><th>Entrega</th><th>Hora</th></tr>
                                </thead>
                                <tbody id="tablaHistorialCliente">
                                    <tr><td colspan="5" style="text-align: center; color: #777;">Aún no hay pedidos registrados.</td></tr>
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            `;
            actualizarDatosCliente();
            conectar(`mesa_${mesaId}`);
        }

        function renderVistaCamarero() {
            appDiv.innerHTML = `
                <div class="box">
                    <button class="btn-logout" onclick="cerrarSesion()">🚪 Cerrar Sesión</button>
                    <h1>🏃‍♂️ Panel de Control de Camareros</h1>
                    <p>Monitoreo en tiempo real de llamadas y gestión de comandas por mesa.</p>
                </div>
                <div class="box">
                    <h2>🔔 Solicitudes Activas</h2>
                    <div id="listaAlertas" style="max-height: 250px; overflow-y: auto;">
                        <p style="color: #777;">No hay solicitudes pendientes.</p>
                    </div>
                </div>
                <div class="box">
                    <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 10px;">
                        <h2 style="margin: 0;">📋 Estado y Comandas de Mesas</h2>
                        <input type="text" id="buscadorMesasCamarero" placeholder="🔍 Buscar mesa..." oninput="renderizarCuentasCamarero()" style="width: 240px;">
                    </div>
                    <div id="resumenCuentas">Cargando mesas...</div>
                </div>
            `;
            cargarProductosCache().then(() => cargarDatosCamarero());
            conectar("camarero_" + Math.random().toString(36).substring(2, 7));
        }

        function renderVistaAdmin() {
            appDiv.innerHTML = `
                <div class="box">
                    <button class="btn-logout" onclick="cerrarSesion()">🚪 Cerrar Sesión</button>
                    <h1>🔔 Registro Global de Solicitudes y Alertas</h1>
                    <div id="listaAlertas" style="max-height: 200px; overflow-y: auto; margin-bottom: 12px;">
                        <p style="color: #777;">No hay solicitudes pendientes.</p>
                    </div>
                    <div id="log"></div>
                </div>
                <div class="box" style="text-align: center; background: #fdfefe;">
                    <h2>📱 Acceso Móvil para Camareros</h2>
                    <p style="color: #666; font-size: 14px;">Escanea este código QR con el celular de cualquier camarero:</p>
                    <div id="qrCamareroContainer">Cargando QR de camarero...</div>
                </div>
                <div class="box">
                    <h1>🍔 Gestión de la Carta (Productos y Precios)</h1>
                    <div style="margin-bottom: 15px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center;">
                        <div class="search-container" style="max-width: 280px;">
                            <input type="text" id="nombreProducto" placeholder="Nombre del producto" oninput="filtrarProductosAdmin(this.value)" style="width: 100%; box-sizing: border-box;">
                            <div id="autocompleteListAdmin" class="autocomplete-list" style="display: none;"></div>
                        </div>
                        <input type="number" id="precioProducto" placeholder="Precio ($)" style="width: 120px;">
                        <button onclick="guardarProducto()" style="background: #27ae60;">Añadir / Actualizar</button>
                        <button onclick="limpiarFormularioProducto()" style="background: #6c757d; padding: 10px 12px;">Cancelar</button>
                    </div>
                    <div style="max-height: 200px; overflow-y: auto;">
                        <table class="table-data">
                            <thead><tr><th>Producto</th><th>Precio</th><th>Acciones</th></tr></thead>
                            <tbody id="tablaProductos"><tr><td colspan="3">Cargando carta...</td></tr></tbody>
                        </table>
                    </div>
                </div>
                <div class="box">
                    <h1>🖥️ Panel Central - Gestión de Mesas y QRs</h1>
                    <div style="margin: 15px 0;">
                        <input type="text" id="numMesa" placeholder="Número o Nombre de Mesa">
                        <button onclick="registrarMesa()">Registrar y Generar QR</button>
                        <a href="/?camarero=1" target="_blank"><button style="background: #e67e22; float: right;">Abrir Vista Camarero 🏃‍♂️</button></a>
                    </div>
                </div>
                <div class="box">
                    <h3>Mesas Registradas y Códigos QR Activos</h3>
                    <div id="gridMesas" class="grid-mesas">Cargando mesas...</div>
                </div>
            `;
            cargarQRCamarero();
            cargarProductosAdmin();
            cargarMesas();
            conectar("admin_" + Math.random().toString(36).substring(2, 7));
        }

        async function cargarQRCamarero() {
            const res = await fetch('/api/qr-camarero');
            const data = await res.json();
            const container = document.getElementById("qrCamareroContainer");
            if (container) {
                container.innerHTML = `
                    <img src="data:image/png;base64,${data.qr}" alt="QR Camarero" style="width: 140px; height: 140px; border: 3px solid white; box-shadow: 0 0 5px rgba(0,0,0,0.1);"><br>
                    <small style="color: #555; word-break: break-all; display: block; margin: 6px 0;">${data.url}</small>
                `;
            }
        }

        async function cargarProductosCache() {
            const res = await fetch('/api/productos');
            listaProductosCache = await res.json();
        }

        async function cargarProductosAdmin() {
            await cargarProductosCache();
            renderizarTablaProductos(listaProductosCache);
        }

        function renderizarTablaProductos(productos) {
            const tbody = document.getElementById("tablaProductos");
            if(!tbody) return;
            if(productos.length === 0) {
                tbody.innerHTML = "<tr><td colspan='3'>No se encontraron productos.</td></tr>";
                return;
            }
            tbody.innerHTML = "";
            productos.forEach(p => {
                tbody.innerHTML += `
                    <tr>
                        <td><b>${p.nombre}</b></td>
                        <td>$${p.precio.toLocaleString()}</td>
                        <td>
                            <button class="btn-edit" onclick="cargarEdicion('${p.nombre}', ${p.precio})">✏️ Editar</button>
                            <button class="btn-delete" onclick="eliminarProducto('${p.nombre}')">🗑️ Eliminar</button>
                        </td>
                    </tr>
                `;
            });
        }

        function filtrarProductosAdmin(query) {
            const listDiv = document.getElementById("autocompleteListAdmin");
            if (!listDiv) return;
            if (!query.trim()) {
                listDiv.style.display = "none";
                renderizarTablaProductos(listaProductosCache);
                return;
            }
            const filtrados = listaProductosCache.filter(p => p.nombre.toLowerCase().includes(query.toLowerCase()));
            renderizarTablaProductos(filtrados);
            if (filtrados.length > 0) {
                listDiv.innerHTML = "";
                filtrados.forEach(p => {
                    const itemDiv = document.createElement("div");
                    itemDiv.className = "autocomplete-item";
                    itemDiv.innerHTML = `<b>${p.nombre}</b> - $${p.precio.toLocaleString()}`;
                    itemDiv.onmousedown = function(e) {
                        e.preventDefault();
                        document.getElementById("nombreProducto").value = p.nombre;
                        document.getElementById("precioProducto").value = p.precio;
                        listDiv.style.display = "none";
                    };
                    listDiv.appendChild(itemDiv);
                });
                listDiv.style.display = "block";
            } else {
                listDiv.style.display = "none";
            }
        }

        function filtrarProductosMesa(mesa, query) {
            const listDiv = document.getElementById(`autocompleteListMesa_${mesa}`);
            if (!listDiv) return;
            if (!query.trim()) {
                listDiv.style.display = "none";
                return;
            }
            const filtrados = listaProductosCache.filter(p => p.nombre.toLowerCase().includes(query.toLowerCase()));
            if (filtrados.length > 0) {
                listDiv.innerHTML = "";
                filtrados.forEach(p => {
                    const itemDiv = document.createElement("div");
                    itemDiv.className = "autocomplete-item";
                    itemDiv.innerHTML = `<b>${p.nombre}</b> - $${p.precio.toLocaleString()}`;
                    itemDiv.onmousedown = function(e) {
                        e.preventDefault();
                        document.getElementById(`inputBusqueda_${mesa}`).value = p.nombre;
                        document.getElementById(`inputPrecio_${mesa}`).value = p.precio;
                        listDiv.style.display = "none";
                    };
                    listDiv.appendChild(itemDiv);
                });
                listDiv.style.display = "block";
            } else {
                listDiv.style.display = "none";
            }
        }

        async function guardarOActualizarItem(mesa) {
            const inputBusqueda = document.getElementById(`inputBusqueda_${mesa}`);
            const inputPrecio = document.getElementById(`inputPrecio_${mesa}`);
            const inputCantidad = document.getElementById(`inputCantidad_${mesa}`);
            const editIdInput = document.getElementById(`editItemId_${mesa}`);
            const btnAccion = document.getElementById(`btnAgregar_${mesa}`);
            
            const nombreItem = inputBusqueda.value.trim();
            const precioItem = parseFloat(inputPrecio.value);
            const cantidadItem = parseInt(inputCantidad.value) || 1;
            const editId = editIdInput ? editIdInput.value : "";

            if (!nombreItem || isNaN(precioItem) || precioItem <= 0) {
                alert("Por favor selecciona un producto válido.");
                return;
            }

            let url = '/api/agregar-item';
            let payload = { mesa: mesa, item: nombreItem, precio: precioItem, cantidad: cantidadItem };

            if (editId) {
                url = '/api/actualizar-item-comanda';
                payload = { id: parseInt(editId), item: nombreItem, precio: precioItem, cantidad: cantidadItem };
            }

            const res = await fetch(url, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });

            const data = await res.json();
            if (data.success) {
                inputBusqueda.value = "";
                inputPrecio.value = "";
                inputCantidad.value = "1";
                if(editIdInput) editIdInput.value = "";
                if(btnAccion) btnAccion.innerText = "➕ Añadir";

                cargarDatosCamarero();

                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({
                        type: 'SYNC_UPDATE',
                        table: mesa,
                        message: editId ? `Ítem modificado: ${cantidadItem}x ${nombreItem}` : `Nuevo pedido: ${cantidadItem}x ${nombreItem}`
                    }));
                }
            }
        }

        function prepararEdicionItem(mesa, id, item, precioUnitario, cantidad) {
            document.getElementById(`inputBusqueda_${mesa}`).value = item;
            const productoEnCache = listaProductosCache.find(p => p.nombre === item);
            const precioReal = productoEnCache ? productoEnCache.precio : (precioUnitario / cantidad);
            
            document.getElementById(`inputPrecio_${mesa}`).value = precioReal;
            document.getElementById(`inputCantidad_${mesa}`).value = cantidad;
            
            let editIdInput = document.getElementById(`editItemId_${mesa}`);
            if (!editIdInput) {
                editIdInput = document.createElement("input");
                editIdInput.type = "hidden";
                editIdInput.id = `editItemId_${mesa}`;
                document.getElementById(`inputBusqueda_${mesa}`).parentNode.appendChild(editIdInput);
            }
            editIdInput.value = id;
            const btnAccion = document.getElementById(`btnAgregar_${mesa}`);
            if (btnAccion) btnAccion.innerText = "💾 Guardar";
        }

        async function eliminarItemComanda(idItem, mesa) {
            if (!confirm("¿Eliminar producto?")) return;
            await fetch(`/api/eliminar-item-comanda?id=${idItem}`, { method: 'DELETE' });
            cargarDatosCamarero();
            
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: 'SYNC_UPDATE', table: mesa, message: "Ítem eliminado de comanda" }));
            }
        }

        async function actualizarEstadoEntrega(idItem, estadoCheck, mesa) {
            await fetch('/api/actualizar-entrega', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ id: idItem, entregado: estadoCheck ? 1 : 0 })
            });
            cargarDatosCamarero();

            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: 'SYNC_UPDATE', table: mesa, message: "Actualización en entrega de ítems" }));
            }
        }

        function cargarEdicion(nombre, precio) {
            document.getElementById("nombreProducto").value = nombre;
            document.getElementById("precioProducto").value = precio;
        }

        function limpiarFormularioProducto() {
            document.getElementById("nombreProducto").value = "";
            document.getElementById("precioProducto").value = "";
            renderizarTablaProductos(listaProductosCache);
        }

        async function guardarProducto() {
            const nombre = document.getElementById("nombreProducto").value.trim();
            const precio = parseFloat(document.getElementById("precioProducto").value);
            if(!nombre || isNaN(precio)) return;
            const res = await fetch('/api/guardar-producto', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ nombre: nombre, precio: precio })
            });
            const data = await res.json();
            if(data.success) {
                limpiarFormularioProducto();
                cargarProductosAdmin();
            }
        }

        async function eliminarProducto(nombre) {
            if(!confirm(`¿Eliminar "${nombre}"?`)) return;
            await fetch(`/api/eliminar-producto?nombre=${encodeURIComponent(nombre)}`, { method: 'DELETE' });
            cargarProductosAdmin();
        }

        async function actualizarDatosCliente() {
            if (!mesaId) return;
            try {
                const resTotal = await fetch(`/api/total-mesa?mesa=${mesaId}`);
                const dataTotal = await resTotal.json();
                document.getElementById("totalMesaCliente").innerText = `$${dataTotal.total.toLocaleString()}`;

                const resHistorial = await fetch(`/api/mesa-items?mesa=${mesaId}`);
                const items = await resHistorial.json();
                const tbody = document.getElementById("tablaHistorialCliente");
                if (items.length === 0) {
                    tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: #777;">Aún no hay pedidos.</td></tr>`;
                    return;
                }
                tbody.innerHTML = "";
                items.forEach(i => {
                    tbody.innerHTML += `
                        <tr>
                            <td><b>${i.cantidad}</b></td>
                            <td><b>${i.item}</b></td>
                            <td>$${(i.precio * i.cantidad).toLocaleString()}</td>
                            <td>${i.entregado ? '✅ Entregado' : '⏳ Pendiente'}</td>
                            <td><small>${i.timestamp}</small></td>
                        </tr>
                    `;
                });
            } catch (e) { console.error(e); }
        }

        async function cargarDatosCamarero() {
            try {
                await cargarProductosCache();
                const res = await fetch('/api/cuentas-activas');
                cacheCuentasCamarero = await res.json();
                renderizarCuentasCamarero();
            } catch (e) { console.error(e); }
        }

        function renderizarCuentasCamarero() {
            const contenedor = document.getElementById("resumenCuentas");
            if (!contenedor) return;
            const filtro = document.getElementById("buscadorMesasCamarero")?.value.toLowerCase().trim() || "";
            const filtrados = cacheCuentasCamarero.filter(g => g.mesa.toLowerCase().includes(filtro));

            if (filtrados.length === 0) {
                contenedor.innerHTML = "<p style='color: #777; text-align: center;'>No hay mesas activas.</p>";
                return;
            }

            let html = "";
            filtrados.forEach(grupo => {
                let filas = "";
                let total = 0;
                grupo.items.forEach(i => {
                    total += i.precio * i.cantidad;
                    filas += `
                        <tr>
                            <td><b>${i.cantidad}</b></td>
                            <td>${i.item}</td>
                            <td>$${(i.precio * i.cantidad).toLocaleString()}</td>
                            <td><input type="checkbox" ${i.entregado ? "checked" : ""} onchange="actualizarEstadoEntrega(${i.id}, this.checked, '${grupo.mesa}')"></td>
                            <td><small>${i.timestamp}</small></td>
                            <td>
                                <button class="btn-edit" onclick="prepararEdicionItem('${grupo.mesa}', ${i.id}, '${i.item}', ${i.precio}, ${i.cantidad})">✏️</button>
                                <button class="btn-delete" onclick="eliminarItemComanda(${i.id}, '${grupo.mesa}')">🗑️</button>
                            </td>
                        </tr>
                    `;
                });

                html += `
                    <div style="border: 1px solid #ccc; border-radius: 6px; padding: 12px; margin-bottom: 15px; background: #fff;">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <h3>Mesa ${grupo.mesa}</h3>
                            <div>
                                <span style="font-weight: bold; color: #27ae60; margin-right: 15px;">Total: $${total.toLocaleString()}</span>
                                <button class="btn-cobrar" onclick="cobrarMesa('${grupo.mesa}')">💳 Cobrar</button>
                            </div>
                        </div>
                        ${filas ? `<table class="table-data"><tr><th>Cant.</th><th>Ítem</th><th>Subtotal</th><th>Entregado</th><th>Hora</th><th>Acciones</th></tr>${filas}</table>` : '<p>Sin consumos.</p>'}
                        <div style="margin-top: 10px; display: flex; gap: 8px;">
                            <input type="text" id="inputBusqueda_${grupo.mesa}" placeholder="Buscar plato..." oninput="filtrarProductosMesa('${grupo.mesa}', this.value)" style="flex:1;">
                            <div id="autocompleteListMesa_${grupo.mesa}" class="autocomplete-list" style="display:none;"></div>
                            <input type="number" id="inputCantidad_${grupo.mesa}" value="1" min="1" style="width: 60px;">
                            <input type="hidden" id="inputPrecio_${grupo.mesa}">
                            <button id="btnAgregar_${grupo.mesa}" onclick="guardarOActualizarItem('${grupo.mesa}')" style="background: #27ae60;">➕ Añadir</button>
                        </div>
                    </div>
                `;
            });
            contenedor.innerHTML = html;
        }

        async function cobrarMesa(mesa) {
            if(!confirm(`¿Cerrar cuenta de la Mesa ${mesa}?`)) return;
            await fetch(`/api/cerrar-cuenta?mesa=${mesa}`, { method: 'DELETE' });
            cargarDatosCamarero();

            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: 'SYNC_UPDATE', table: mesa, message: "Cuenta cobrada y cerrada" }));
            }
        }

        async function registrarMesa() {
            const mesa = document.getElementById("numMesa").value.trim();
            if(!mesa) return;
            await fetch(`/api/crear-mesa?mesa=${mesa}`, { method: 'POST' });
            document.getElementById("numMesa").value = "";
            cargarMesas();
        }

        async function eliminarMesa(mesa) {
            if(!confirm(`¿Eliminar mesa ${mesa}?`)) return;
            await fetch(`/api/eliminar-mesa?mesa=${mesa}`, { method: 'DELETE' });
            cargarMesas();
        }

        async function cargarMesas() {
            const res = await fetch('/api/mesas');
            const mesas = await res.json();
            const grid = document.getElementById("gridMesas");
            if (!grid) return;
            if (mesas.length === 0) {
                grid.innerHTML = "<p>No hay mesas.</p>";
                return;
            }
            grid.innerHTML = "";
            mesas.forEach(m => {
                grid.innerHTML += `
                    <div class="card-mesa">
                        <h4>Mesa ${m.numero}</h4>
                        <img src="data:image/png;base64,${m.qr}"><br>
                        <button class="btn-delete" onclick="eliminarMesa('${m.numero}')">🗑️</button>
                    </div>
                `;
            });
        }

        function conectar(clientId) {
            const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            ws = new WebSocket(`${proto}//${window.location.host}/ws/${clientId}`);
            
            ws.onopen = () => {
                console.log("WebSocket conectado con éxito");
            };

            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                
                if (esCamarero || !mesaId) {
                    cargarDatosCamarero();
                    if (data.message && data.type !== 'SYNC_UPDATE') {
                        agregarAlertaVisual(data);
                    }
                }
                
                if (mesaId && data.table === mesaId) {
                    actualizarDatosCliente();
                }
            };

            ws.onerror = (error) => {
                console.error("Error en WebSocket:", error);
            };

            ws.onclose = () => {
                console.log("WebSocket desconectado. Reintentando...");
                setTimeout(() => conectar(clientId), 3000);
            };
        }

        function agregarAlertaVisual(data) {
            const lista = document.getElementById("listaAlertas");
            if (!lista) return;
            if (lista.innerHTML.includes("No hay solicitudes")) lista.innerHTML = "";
            const div = document.createElement("div");
            div.className = "alerta-item";
            div.innerHTML = `<div><b>Mesa ${data.table}:</b> ${data.message}</div><button class="btn-atendido" onclick="this.parentElement.remove()">✔</button>`;
            lista.prepend(div);
        }

        async function enviarAccionWS(tipo, desc) {
            if (!mesaId) return;
            
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({
                    type: tipo,
                    table: mesaId,
                    message: desc,
                    timestamp: new Date().toLocaleTimeString()
                }));
                alert("¡Solicitud enviada con éxito a los camareros!");
            } else {
                conectar("mesa_" + mesaId);
                setTimeout(() => {
                    if (ws && ws.readyState === WebSocket.OPEN) {
                        ws.send(JSON.stringify({
                            type: tipo,
                            table: mesaId,
                            message: desc,
                            timestamp: new Date().toLocaleTimeString()
                        }));
                        alert("¡Solicitud enviada con éxito a los camareros!");
                    } else {
                        alert("No se pudo establecer conexión en tiempo real con el servidor. Verifica tu red.");
                    }
                }, 1000);
            }
        }

        iniciarApp();
    </script>
</body>
</html>
"""

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        if websocket not in self.active_connections:
            self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_text(json.dumps(message))
            except Exception:
                pass

manager = ConnectionManager()

# --- ENDPOINTS DE AUTENTICACIÓN Y SESIÓN ---

@app.post("/api/register")
async def register(data: dict):
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    rol = data.get("rol", "camarero")
    if not username or not password:
        return {"success": False, "error": "Faltan campos obligatorios."}
    
    conn = sqlite3.connect("pos_local.db")
    cursor = conn.cursor()
    try:
        hashed_pwd = hash_password(password)
        cursor.execute("INSERT INTO usuarios (username, password, rol) VALUES (?, ?, ?)", (username, hashed_pwd, rol))
        conn.commit()
        success = True
        error = None
    except sqlite3.IntegrityError:
        success = False
        error = "El nombre de usuario ya está registrado."
    finally:
        conn.close()
    return {"success": success, "error": error}

@app.post("/api/login")
async def login(data: dict, response: Response):
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    hashed_pwd = hash_password(password)

    conn = sqlite3.connect("pos_local.db")
    cursor = conn.cursor()
    cursor.execute("SELECT rol FROM usuarios WHERE username = ? AND password = ?", (username, hashed_pwd))
    user = cursor.fetchone()
    conn.close()

    if user:
        rol = user[0]
        response.set_cookie(key="session_user", value=username, httponly=True)
        response.set_cookie(key="session_rol", value=rol, httponly=True)
        return {"success": True, "rol": rol}
    return {"success": False, "error": "Usuario o contraseña incorrectos."}

@app.post("/api/logout")
async def logout(response: Response):
    response.delete_cookie("session_user")
    response.delete_cookie("session_rol")
    return {"success": True}

@app.get("/api/check-session")
async def check_session(session_user: str = Cookie(default=None), session_rol: str = Cookie(default=None)):
    if session_user and session_rol:
        return {"logged": True, "username": session_user, "rol": session_rol}
    return {"logged": False}

# --- ENDPOINTS GENERALES Y DE NEGOCIO ---

@app.get("/", response_class=HTMLResponse)
async def get(request: Request):
    return HTML_TEMPLATE

@app.get("/api/qr-camarero")
async def qr_camarero(request: Request):
    base_url = str(request.base_url).rstrip("/")
    target_url = f"{base_url}/?camarero=1"
    qr = qrcode.QRCode(box_size=10, border=2)
    qr.add_data(target_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    return {"url": target_url, "qr": base64.b64encode(buffered.getvalue()).decode("utf-8")}

@app.get("/api/productos")
async def obtener_productos():
    conn = sqlite3.connect("pos_local.db")
    cursor = conn.cursor()
    cursor.execute("SELECT nombre, precio FROM productos ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    return [{"nombre": r[0], "precio": r[1]} for r in rows]

@app.post("/api/guardar-producto")
async def guardar_producto(data: dict):
    conn = sqlite3.connect("pos_local.db")
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT OR REPLACE INTO productos (nombre, precio) VALUES (?, ?)", (data.get("nombre"), data.get("precio")))
        conn.commit()
        success = True
    except Exception:
        success = False
    finally:
        conn.close()
    return {"success": success}

@app.delete("/api/eliminar-producto")
async def eliminar_producto(nombre: str):
    conn = sqlite3.connect("pos_local.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM productos WHERE nombre = ?", (nombre,))
    conn.commit()
    conn.close()
    return {"success": True}

@app.post("/api/agregar-item")
async def agregar_item(data: dict):
    conn = sqlite3.connect("pos_local.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO cuenta_mesas (mesa, item, precio, cantidad, entregado, timestamp) VALUES (?, ?, ?, ?, 0, ?)", 
                   (data.get("mesa"), data.get("item"), data.get("precio"), data.get("cantidad", 1), datetime.datetime.now().strftime("%H:%M:%S")))
    conn.commit()
    conn.close()
    return {"success": True}

@app.post("/api/actualizar-item-comanda")
async def actualizar_item_comanda(data: dict):
    conn = sqlite3.connect("pos_local.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE cuenta_mesas SET item = ?, precio = ?, cantidad = ? WHERE id = ?", (data.get("item"), data.get("precio"), data.get("cantidad", 1), data.get("id")))
    conn.commit()
    conn.close()
    return {"success": True}

@app.delete("/api/eliminar-item-comanda")
async def eliminar_item_comanda(id: int):
    conn = sqlite3.connect("pos_local.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM cuenta_mesas WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return {"success": True}

@app.post("/api/actualizar-entrega")
async def actualizar_entrega(data: dict):
    conn = sqlite3.connect("pos_local.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE cuenta_mesas SET entregado = ? WHERE id = ?", (data.get("entregado"), data.get("id")))
    conn.commit()
    conn.close()
    return {"success": True}

@app.get("/api/cuentas-activas")
async def cuentas_activas():
    conn = sqlite3.connect("pos_local.db")
    cursor = conn.cursor()
    cursor.execute("SELECT numero_mesa FROM mesas ORDER BY id ASC")
    mesas_registradas = cursor.fetchall()
    resultado = []
    for m in mesas_registradas:
        num_mesa = m[0]
        cursor.execute("SELECT id, item, precio, cantidad, entregado, timestamp FROM cuenta_mesas WHERE mesa = ? AND item IS NOT NULL", (num_mesa,))
        rows = cursor.fetchall()
        items_lista = [{"id": r[0], "item": r[1], "precio": r[2], "cantidad": r[3], "entregado": r[4], "timestamp": r[5]} for r in rows]
        resultado.append({"mesa": num_mesa, "items": items_lista})
    conn.close()
    return resultado

@app.get("/api/mesa-items")
async def mesa_items(mesa: str):
    conn = sqlite3.connect("pos_local.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, item, precio, cantidad, entregado, timestamp FROM cuenta_mesas WHERE mesa = ? AND item IS NOT NULL", (mesa,))
    rows = cursor.fetchall()
    conn.close()
    return [{"id": r[0], "item": r[1], "precio": r[2], "cantidad": r[3], "entregado": r[4], "timestamp": r[5]} for r in rows]

@app.get("/api/total-mesa")
async def total_mesa(mesa: str):
    conn = sqlite3.connect("pos_local.db")
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(precio * cantidad) FROM cuenta_mesas WHERE mesa = ? AND item IS NOT NULL", (mesa,))
    resultado = cursor.fetchone()[0]
    conn.close()
    return {"total": resultado if resultado else 0.0}

@app.delete("/api/cerrar-cuenta")
async def cerrar_cuenta(mesa: str):
    conn = sqlite3.connect("pos_local.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM cuenta_mesas WHERE mesa = ?", (mesa,))
    conn.commit()
    conn.close()
    return {"success": True}

@app.post("/api/crear-mesa")
async def crear_mesa(mesa: str):
    conn = sqlite3.connect("pos_local.db")
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT 1 FROM mesas WHERE numero_mesa = ?", (mesa,))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO mesas (numero_mesa) VALUES (?)", (mesa,))
            conn.commit()
        success = True
    except Exception:
        success = False
    finally:
        conn.close()
    return {"success": success}

@app.delete("/api/eliminar-mesa")
async def eliminar_mesa(mesa: str):
    conn = sqlite3.connect("pos_local.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM mesas WHERE numero_mesa = ?", (mesa,))
    cursor.execute("DELETE FROM cuenta_mesas WHERE mesa = ?", (mesa,))
    conn.commit()
    conn.close()
    return {"success": True}

@app.get("/api/mesas")
async def obtener_mesas(request: Request):
    conn = sqlite3.connect("pos_local.db")
    cursor = conn.cursor()
    cursor.execute("SELECT numero_mesa FROM mesas ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    base_url = str(request.base_url).rstrip("/")
    resultado = []
    for row in rows:
        num_mesa = row[0]
        target_url = f"{base_url}/?mesa={num_mesa}"
        qr = qrcode.QRCode(box_size=10, border=2)
        qr.add_data(target_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buffered = io.BytesIO()
        img.save(buffered, format="PNG")
        resultado.append({"numero": num_mesa, "url": target_url, "qr": base64.b64encode(buffered.getvalue()).decode("utf-8")})
    return resultado

@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    await manager.connect(websocket)
    try:
        while True:
            text_data = await websocket.receive_text()
            event_data = json.loads(text_data)
            await manager.broadcast(event_data)
    except WebSocketDisconnect:
        manager.disconnect(websocket)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run("app:app", host="0.0.0.0", port=port)