window.chartInterop = {
    _charts: {},

    renderLine: function (canvasId, labels, occupied, total) {
        const canvas = document.getElementById(canvasId);
        if (!canvas) return;

        if (this._charts[canvasId]) {
            this._charts[canvasId].destroy();
            delete this._charts[canvasId];
        }

        const ctx = canvas.getContext('2d');
        this._charts[canvasId] = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Occupied',
                        data: occupied,
                        borderColor: '#1B4F8A',
                        backgroundColor: 'rgba(27,79,138,0.08)',
                        tension: 0.3,
                        fill: true,
                        pointRadius: 2,
                        pointHoverRadius: 5
                    },
                    {
                        label: 'Total Capacity',
                        data: total,
                        borderColor: '#CBD5E1',
                        borderDash: [6, 4],
                        tension: 0,
                        fill: false,
                        pointRadius: 0
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: { font: { size: 12 }, color: '#64748B' }
                    }
                },
                scales: {
                    x: {
                        ticks: { maxTicksLimit: 8, maxRotation: 0, color: '#64748B', font: { size: 11 } },
                        grid: { color: 'rgba(0,0,0,.04)' }
                    },
                    y: {
                        beginAtZero: true,
                        ticks: { color: '#64748B', font: { size: 11 } },
                        grid: { color: 'rgba(0,0,0,.04)' }
                    }
                }
            }
        });
    }
};

window.blazorScrollBottom = function (element) {
    if (element) element.scrollTop = element.scrollHeight;
};
