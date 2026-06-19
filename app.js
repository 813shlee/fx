fetch("rates.json")
.then(r => r.json())
.then(data => {

    const latest = data[data.length - 1];

    // KPI
    document.getElementById("usdRub").innerText =
        latest.usd_rub.toFixed(2);

    document.getElementById("krwRub").innerText =
        latest.krw_rub.toFixed(2);

    document.getElementById("krwToRub").innerText =
        (1000000 / latest.krw_rub).toFixed(0) + " RUB";

    // 판단
    const prev = data[data.length - 2];

    let status = "";

    if (latest.usd_rub > prev.usd_rub && latest.krw_rub < prev.krw_rub) {
        status = "⭐ 최고 상황 (둘 다 환전 유리)";
    } else if (latest.usd_rub > prev.usd_rub) {
        status = "달러 환전 유리";
    } else if (latest.krw_rub < prev.krw_rub) {
        status = "원화 환전 유리";
    } else {
        status = "둘 다 보류";
    }

    document.getElementById("status").innerText = status;

    // 그래프
    const labels = data.map(x => x.date);

    new Chart(document.getElementById("chart"), {
        type: "line",
        data: {
            labels: labels,
            datasets: [
                {
                    label: "USD/RUB",
                    data: data.map(x => x.usd_rub),
                    borderColor: "orange",
                    tension: 0.3,
                    pointRadius: 4
                },
                {
                    label: "KRW/RUB",
                    data: data.map(x => x.krw_rub),
                    borderColor: "cyan",
                    tension: 0.3,
                    pointRadius: 4,
                    yAxisID: "y1"
                }
            ]
        },
        options: {
            layout: {
                padding: {
                    left: 20,
                    right: 20
                }
            },
            plugins: {
                tooltip: {
                    enabled: true
                }
            },
            scales: {
                y: { position: "left" },
                y1: { position: "right" }
            }
        }
    });

});