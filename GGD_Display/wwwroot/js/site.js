
let lockedCanvasId = null;

// SignalR connection setup and management
const connection = new signalR.HubConnectionBuilder()
    .withUrl("/twitchhub")
    .build();

connection.on("updateStreamer", streamer => {
    for (let i = 0; i < 16; i++) {
        const dropdown = document.getElementById(`streamerSelectionNode-${i}`);
        if (dropdown && dropdown.value === streamer.privateId) {
            const liveDot = document.getElementById(`liveStatus-${i}`);
            if (liveDot) {
                liveDot.classList.remove("bg-green-500", "bg-red-500");
                liveDot.classList.add(streamer.isLive ? "bg-green-500" : "bg-red-500");
                liveDot.title = streamer.isLive ? "Live" : "Offline";
            }
        }
    }
});

connection.start();

// #region Accordion functionality
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

function toggleAccordion(button) {
    const content = button.nextElementSibling;
    content.style.display = content.style.display === "block" ? "none" : "block";
}

// #endregion Accordion functionality



// #region Disclaimers
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
// #endregion Disclaimers


// #region Canvas Controlls
function enlargeCanvas(id) {
    if (lockedCanvasId === null) {
        const el = document.getElementById('canvas-' + id);
        el.classList.add('enlarged');
    }
}

function shrinkCanvas(id) {
    if (lockedCanvasId === null) {
        const el = document.getElementById('canvas-' + id);
        el.classList.remove('enlarged');
    }
}

function lockCanvas(id) {
    // Don't lock if something is already locked
    if (lockedCanvasId !== null) return;

    const el = document.getElementById('canvas-' + id);
    const icon = document.getElementById('lock-icon-' + id);

    lockedCanvasId = id;
    el.classList.add('enlarged');
    icon.classList.remove('hidden');
}

function toggleLockCanvas(id) {
    const el = document.getElementById('canvas-' + id);
    const icon = document.getElementById('lock-icon-' + id);

    if (lockedCanvasId === id) {
        // Unlock current
        lockedCanvasId = null;
        el.classList.remove('enlarged');
        icon.classList.add('hidden');
    } else {
        // Lock new, remove any existing lock
        if (lockedCanvasId !== null) {
            const prev = document.getElementById('canvas-' + lockedCanvasId);
            const prevIcon = document.getElementById('lock-icon-' + lockedCanvasId);
            prev?.classList.remove('enlarged');
            prevIcon?.classList.add('hidden');
        }

        lockedCanvasId = id;
        el.classList.add('enlarged');
        icon.classList.remove('hidden');
    }
}

function submitStreamers() {
    const input = document.getElementById("streamerInput").value;
    const names = input
        .split('\n')
        .map(line => line.trim())
        .filter(line => line.length > 0);

    fetch('/AddStreamers?handler=Add', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'RequestVerificationToken': document.querySelector('input[name="__RequestVerificationToken"]').value
        },
        body: JSON.stringify({ streamers: names })
    })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                alert(`Added ${data.added} streamer(s).`);
                closeStreamerModal();
                location.reload();
            } else {
                alert("Failed to add streamers.");
            }
        })
        .catch(err => alert("Error: " + err.message));
}

function openStreamerModal() {
    const modal = document.getElementById("addStreamerModal");
    modal.classList.remove("hidden");
    modal.classList.add("flex");
}

function closeStreamerModal() {
    const modal = document.getElementById("addStreamerModal");
    modal.classList.add("hidden");
    modal.classList.remove("flex");
}

// #region Color Picker and Node Color Update
function showColorPicker(nodeId, event, isAlt = false) {
    const picker = document.getElementById(isAlt ? `altColorPicker-${nodeId}` : `colorPicker-${nodeId}`);
    if (!picker) return;

    const x = event.clientX;
    const y = event.clientY;

    picker.style.left = `${x}px`;
    picker.style.top = `${y}px`;
    picker.classList.remove("hidden");
    //picker.focus();
    picker.click();
}

function updateNodeColor(nodeId, hexColor, isAlt = false) {
    debugger;

    fetch('/Index?handler=UpdateNodeColor', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'RequestVerificationToken': document.querySelector('input[name="__RequestVerificationToken"]').value
        },
        body: JSON.stringify({
            nodeId: nodeId,
            colorHex: hexColor,
            isAlt: isAlt
        })
    })
        .then(res => res.json())
        .then(data => {
            if (!data.success) return alert("Color update failed.");

            // Only update the correct box
            const targetId = isAlt ? `altColorBox-${nodeId}` : `colorBox-${nodeId}`;
            const box = document.getElementById(targetId);
            if (box) box.style.backgroundColor = hexColor;
        })
        .catch(err => alert("Error updating color: " + err.message));
}


function updateLinkedStreamer(nodeId, privateId) {
    console.log("Sending streamer update:", { nodeId, privateId });

    fetch('/Index?handler=UpdateNodeStreamer', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'RequestVerificationToken': document.querySelector('input[name="__RequestVerificationToken"]').value
        },
        body: JSON.stringify({ nodeId, privateId })
    })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                // Update color in UI
                const colorBox = document.getElementById(`colorBox-${nodeId}`);
                if (colorBox) {
                    colorBox.style.backgroundColor = data.colorHex;
                }

                // Update streamer name
                const title = document.getElementById(`canvas-title-${nodeId}`);
                if (title) {
                    title.innerText = data.streamerName ?? `Canvas ${nodeId}`;
                }

                // Update live status dot
                const liveDot = document.getElementById(`liveStatus-${nodeId}`);
                if (liveDot) {
                    liveDot.classList.remove("bg-green-500", "bg-red-500", "animate-pulse");
                    liveDot.classList.add(data.isLive ? "bg-green-500" : "bg-red-500");
                    liveDot.title = data.isLive ? "Live" : "Offline";

                }
            } else {
                alert("Update failed: " + (data.message || "Unknown error."));
            }
        })
        .catch(err => alert("Error updating streamer: " + err.message));
}


// #region Settings and Options
function openSettingsModal() {
    document.getElementById("settingsModal").style.display = "block";
}

function closeSettingsModal() {
    document.getElementById("settingsModal").style.display = "none";
}

function toggleAdvancedSettings() {
    const checkbox = document.getElementById("enableAdvancedSettings");
    const section = document.getElementById("advancedSettingsSection");

    if (checkbox.checked) {
        section.classList.remove("hidden");
    } else {
        section.classList.add("hidden");
    }
}

function toggleSettingsModal() {
    if (document.getElementById("settingsModal").classList.contains("hidden")) {
        openSettingsModal();
    } else {
        closeSettingsModal();
    }
}

function refreshStreamerIds() {
    if (!confirm("This will refresh streamer IDs using the Twitch API. Continue?")) return;

    fetch('/Index?handler=RefreshIds', {
        method: 'POST',
        headers: {
            'RequestVerificationToken': document.querySelector('input[name="__RequestVerificationToken"]').value
        }
    })
        .then(res => res.json())
        .then(data => {
            alert(`Updated ${data.updated} streamer(s).`);
            location.reload();
        })
        .catch(err => alert("Error refreshing streamer IDs: " + err.message));
}

// #endregion Settings and Options
