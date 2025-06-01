
let lockedCanvasId = null;

// SignalR connection setup and management
//const connection = new signalR.HubConnectionBuilder()
//    .withUrl("/twitchhub")
//    .build();

//connection.on("updateStreamer", streamer => {
//    for (let i = 0; i < 16; i++) {
//        const dropdown = document.getElementById(`streamerSelectionNode-${i}`);
//        if (dropdown && dropdown.value === streamer.privateId) {
//            const liveDot = document.getElementById(`liveStatus-${i}`);
//            if (liveDot) {
//                liveDot.classList.remove("bg-green-500", "bg-red-500", "animate-pulse");
//                liveDot.classList.add(streamer.isLive ? "bg-green-500 animate-pulse" : "bg-red-500");
//                liveDot.title = streamer.isLive ? "Live" : "Offline";
//            }
//        }
//    }
//});

//connection.start();
// #region Global event listeners
document.addEventListener('dblclick', () => {
    if (lockedCanvasId !== null) {
        unlockCanvas(lockedCanvasId);
        lockedCanvasId = null;
    }
});

document.addEventListener('click', function (e) {
    document.querySelectorAll('input[type="color"]').forEach(picker => {
        if (!picker.contains(e.target)) {
            picker.classList.add('hidden');
        }
    });
});
// #endregion Global event listeners

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


// #region Canvas Controls
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
    const nav = document.getElementById('mainNav');

    lockedCanvasId = id;
    el.classList.add('enlarged');
    icon.classList.remove('hidden');
    nav.classList.add('nav-disabled');
    disableOtherCanvasesInteraction(id);
}

function unlockCanvas(id) {
    const el = document.getElementById('canvas-' + id);
    const icon = document.getElementById('lock-icon-' + id);
    const nav = document.getElementById('mainNav');

    if (lockedCanvasId === id) {
        // Unlock current
        lockedCanvasId = null;
        el.classList.remove('enlarged');
        icon.classList.add('hidden');
        nav.classList.remove('nav-disabled');
        enableAllCanvasesInteraction();
    }
}

function disableOtherCanvasesInteraction(lockedId) {
    document.querySelectorAll('.canvas-box').forEach((el, index) => {
        if (index !== lockedId) {
            el.classList.add('pointer-events-none', 'opacity-50');
        }
    });
}

function enableAllCanvasesInteraction() {
    document.querySelectorAll('.canvas-box').forEach(el => {
        el.classList.remove('pointer-events-none', 'opacity-50');
    });
}

// #endregion 


// #region Color Picker and Node Color Update
function showColorPicker(nodeId, event, isAlt) {
    event.stopPropagation();

    const pickerId = isAlt ? `altColorPicker-${nodeId}` : `colorPicker-${nodeId}`;
    const picker = document.getElementById(pickerId);
    const canvas = document.getElementById(`canvas-${nodeId}`);

    if (!picker || !canvas) return;

    // Get canvas bounds
    const canvasRect = canvas.getBoundingClientRect();

    // Calculate position within canvas (clamp near top-left or mouse position)
    const offsetX = event.clientX - canvasRect.left;
    const offsetY = event.clientY - canvasRect.top;

    // Constrain picker position inside canvas
    const posX = Math.min(offsetX, canvasRect.width - 160); // prevent overflow
    const posY = Math.min(offsetY, canvasRect.height - 40);

    // Apply position and show
    picker.style.left = `${posX}px`;
    picker.style.top = `${posY}px`;
    picker.classList.remove("hidden");
    //picker.focus(); // Optional: keep or remove if causing issues 
    picker.click();
}

function updateNodeColor(nodeId, hexColor, isAlt = false) {

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
    // Hide the color picker after selection
    const pickerId = isAlt ? `altColorPicker-${nodeId}` : `colorPicker-${nodeId}`;
    const picker = document.getElementById(pickerId);
    if (picker) {
        picker.classList.add('hidden');  // hide via Tailwind
        picker.blur();                   // remove focus to force close
    }
}

async function updateNodeEffect(nodeId, newColor, newEffect) {
    const payload = {
        nodeId: nodeId,
        colorHex: newColor,
        displaySetting: newEffect
    };

    const response = await fetch("/Index?handler=UpdateEffect", {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "RequestVerificationToken": document.querySelector("input[name='__RequestVerificationToken']").value
        },
        body: JSON.stringify(payload)
    });

    if (response.ok) {
        console.log("Node updated!");
    } else {
        console.error("Failed to update node.");
    }
}
// #endregion


//#region Streamer Settings and Modifications
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
                // update streamer name and link
                const nameText = document.getElementById(`streamerNameText-${nodeId}`);
                if (nameText) {
                    nameText.textContent = data.streamerName ?? `Canvas ${nodeId}`;
                }

                // link :: TODO: add the link to the streamers page in the json, so when more sites are added it can be updated 
                const nameLink = document.getElementById(`streamerNameLink-${nodeId}`);
                if (nameLink && data.streamerName) {
                    nameLink.href = `https://twitch.tv/${data.streamerName}`;
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

function removeStreamer(privateId) {
    fetch('?handler=RemoveStreamer', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'RequestVerificationToken': document.querySelector('input[name="__RequestVerificationToken"]').value
        },
        body: JSON.stringify({ privateId })
    })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                location.reload();
            } else {
                alert("Failed to remove streamer: " + (data.message || ""));
            }
        })
        .catch(err => alert("Error removing streamer: " + err.message));
}

// #endregion


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


function togglePreviewModal() {
    const modal = document.getElementById('previewModal');
    if (modal) modal.classList.toggle('hidden');
}

function toggleAdultContentSetting(isEnabled) {
    fetch('/Settings?handler=ToggleAdultSetting', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'RequestVerificationToken': document.querySelector('input[name="__RequestVerificationToken"]').value
        },
        body: JSON.stringify({ enabled: isEnabled })
    }).then(res => res.json())
        .then(data => {
            if (!data.success) {
                alert("Failed to update setting.");
            }
        });
}



// #endregion Settings and Options
