function formatDate() {
    const d = new Date();
    return d.toISOString().split("T")[0];
}

function refreshPage() {
    location.reload();
}

fetch("rates.json")
.then(r => r.json())
.then(data => {

    const latest = data[data.length - 1];

    document.getElementById("today").innerText =
        "Today: " + formatDate();

    // KPI
    document.getElementById("usdRub").innerText =
        latest.usd_rub.toFixed(2);

    document.getElementById("krwRub").innerText =
        latest.krw_rub.toFixed(2);

    // 150만원 → RUB
    document.getElementById("krwToRub").innerText =
        (1500000 / latest.krw_rub).toFixed(0) + " RUB";

    // 1000$ → RUB
    document.getElementById("usdToRub").innerText =
        (1000 * latest.usd_rub).toFixed(0) + " RUB";

    // 그래프 확대용 padding
    const ctx = document.getElementById("chart");

    new Chart(ctx, {
        type: "line",
        data: {
            labels: data.map(x => x.date),
            datasets: [
                {
                    label: "USD/RUB",
                    data: data.map(x => x.usd_rub),
                    borderColor: "orange",
                    pointRadius: 5,
                    tension: 0.3
                },
                {
                    label: "KRW/RUB",
                    data: data.map(x => x.krw_rub),
                    borderColor: "cyan",
                    pointRadius: 5,
                    tension: 0.3,
                    yAxisID: "y1"
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,

            layout: {
                padding: {
                    left: 40,
                    right: 40,
                    top: 20,
                    bottom: 20
                }
            },

            scales: {
                y: { position: "left" },
                y1: { position: "right" }
            },

            plugins: {
                tooltip: { enabled: true }
            }
        }
    });

});
