fetch("rates.json")
  .then(res => res.json())
  .then(data => {

    const latest = data[data.length - 1];

    document.getElementById("usdRub").innerText =
      latest.usd_rub.toFixed(2);

    document.getElementById("krwRub").innerText =
      latest.krw_rub.toFixed(2);

    document.getElementById("krwToRub").innerText =
      (1000000 / latest.krw_rub).toFixed(0) + " RUB";

    const labels = data.map(x => x.date);

    const usd = data.map(x => x.usd_rub);
    const krw = data.map(x => x.krw_rub);

    new Chart(document.getElementById("chart"), {
      type: 'line',
      data: {
        labels: labels,
        datasets: [
          {
            label: "USD/RUB",
            data: usd,
            borderColor: "orange"
          },
          {
            label: "KRW/RUB",
            data: krw,
            borderColor: "cyan",
            yAxisID: 'y1'
          }
        ]
      },
      options: {
        scales: {
          y: { position: 'left' },
          y1: { position: 'right' }
        }
      }
    });

  });