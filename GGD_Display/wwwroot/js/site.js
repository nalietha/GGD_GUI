// Please see documentation at https://learn.microsoft.com/aspnet/core/client-side/bundling-and-minification
// for details on configuring this project to bundle and minify static web assets.

// Write your JavaScript code.

const accordions = document.querySelectorAll(".accordion");
accordions.forEach(button => {
    button.addEventListener("click", () => {
        button.classList.toggle("active");
        const content = button.nextElementSibling;
        if (content.style.display === "block") {
            content.style.display = "none";
        } else {
            content.style.display = "block";
        }
    });
});


function showWifiDisclaimer() {
    document.getElementById("wifiDisclaimer").classList.remove("hidden");
}

function toggleWifiSettings() {
    const checked = document.getElementById("acknowledgeDisclaimer").checked;
    const settings = document.getElementById("wifiSettings");
    if (checked) {
        settings.classList.remove("hidden");
    } else {
        settings.classList.add("hidden");
    }
}
function confirmWifiChange() {
    const username = document.getElementById("wifiUsername").value;
    const password = document.getElementById("wifiPassword").value;
    alert(`WiFi settings submitted:\nUsername: ${username}\nPassword: ${'*'.repeat(password.length)}`);
}


function toggleAccordion(button) {
    const content = button.nextElementSibling;
    content.style.display = content.style.display === "block" ? "none" : "block";
}

function lockEnlargement(id) {
    lockedCanvasId = id;
    const el = document.getElementById('canvas-' + id);
    el.classList.add('enlarged');

    // Show the color picker
    const picker = document.getElementById('colorPicker-' + id);
    picker.style.display = 'block';
}

function submitColor(id, color) {
    // Hide picker
    const picker = document.getElementById('colorPicker-' + id);
    picker.style.display = 'none';

    // Submit via hidden form
    const form = document.createElement('form');
    form.method = 'post';
    form.action = '?handler=ChangeColor';

    const indexInput = document.createElement('input');
    indexInput.type = 'hidden';
    indexInput.name = 'Index';
    indexInput.value = id;

    const colorInput = document.createElement('input');
    colorInput.type = 'hidden';
    colorInput.name = 'ColorHex';
    colorInput.value = color;

    form.appendChild(indexInput);
    form.appendChild(colorInput);

    document.body.appendChild(form);
    form.submit();
}