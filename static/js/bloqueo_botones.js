
(() => {
    "use strict";

    // Evita instalar el bloqueo más de una vez.
    if (window.__bloqueoBotonesInstalado) return;
    window.__bloqueoBotonesInstalado = true;

    let procesando = false;

    function bloquearBotones(excepto = null) {
        document.querySelectorAll(
            'button, input[type="submit"], input[type="button"], input[type="image"]'
        ).forEach((boton) => {
            if (boton === excepto) return;

            if (!boton.disabled) {
                boton.dataset.bloqueoGlobal = "1";
                boton.disabled = true;
            }
        });
    }

    function desbloquearBotones() {
        document.querySelectorAll('[data-bloqueo-global="1"]').forEach((boton) => {
            boton.disabled = false;
            delete boton.dataset.bloqueoGlobal;
        });
    }

    // Bloquea los botones al enviar un formulario válido.
    document.addEventListener("submit", (evento) => {
        const formulario = evento.target;

        if (!(formulario instanceof HTMLFormElement)) return;
        if (evento.defaultPrevented || procesando) return;

        procesando = true;
        bloquearBotones();
    }, true);

    // Bloquea al pulsar botones que ejecutan acciones.
    document.addEventListener("click", (evento) => {
        const boton = evento.target.closest(
            'button, input[type="button"], input[type="image"]'
        );

        if (!boton || boton.disabled || procesando) return;

        // No bloquear botones que solo abren o cierran ventanas.
        if (
            boton.hasAttribute("data-no-global-lock") ||
            boton.getAttribute("data-bs-toggle") === "modal" ||
            boton.getAttribute("data-bs-dismiss") === "modal"
        ) return;

        // Los botones de formulario se controlan al enviarlo.
        if (boton.type === "submit" && boton.form) return;

        procesando = true;
        bloquearBotones();
    }, true);

    // Si la página vuelve desde la caché del navegador,
    // reactiva los botones.
    window.addEventListener("pageshow", () => {
        procesando = false;
        desbloquearBotones();
    });

    // Permite que una acción JavaScript reactive los botones
    // cuando termine correctamente o falle.
    window.finalizarBloqueoBotones = () => {
        procesando = false;
        desbloquearBotones();
    };
})();
