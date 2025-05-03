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
