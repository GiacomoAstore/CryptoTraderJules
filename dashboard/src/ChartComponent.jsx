import { useEffect, useRef } from 'react';
import { createChart } from 'lightweight-charts';

export default function ChartComponent({ symbol, tickData }) {
    const chartContainerRef = useRef();
    const chartRef = useRef();
    const lineSeriesRef = useRef();

    useEffect(() => {
        const handleResize = () => {
            chartRef.current.applyOptions({ width: chartContainerRef.current.clientWidth });
        };

        const chart = createChart(chartContainerRef.current, {
            layout: {
                background: { type: 'solid', color: '#1E1E1E' },
                textColor: '#DDD',
            },
            grid: {
                vertLines: { color: '#2B2B43' },
                horzLines: { color: '#2B2B43' },
            },
            width: chartContainerRef.current.clientWidth,
            height: 400,
        });

        const lineSeries = chart.addLineSeries({
            color: '#2962FF',
            lineWidth: 2,
        });

        chartRef.current = chart;
        lineSeriesRef.current = lineSeries;

        window.addEventListener('resize', handleResize);

        return () => {
            window.removeEventListener('resize', handleResize);
            chart.remove();
        };
    }, []);

    useEffect(() => {
        if (tickData && tickData.symbol.toLowerCase() === symbol.toLowerCase() && lineSeriesRef.current) {
            // Update chart with new tick
            // lightweight-charts expects time in seconds (UNIX timestamp)
            const time = Math.floor(tickData.timestamp_ms / 1000);

            // Try to update using a try-catch because Lightweight Charts throws if times are not strictly ascending
            try {
                lineSeriesRef.current.update({
                    time: time,
                    value: tickData.price,
                });
            } catch (err) {
                console.warn("Tick ignored for chart (time not strictly ascending):", err.message);
            }
        }
    }, [tickData, symbol]);

    return (
        <div style={{ padding: '20px', backgroundColor: '#1E1E1E', borderRadius: '8px' }}>
            <h3 style={{ color: 'white', margin: '0 0 10px 0' }}>{symbol.toUpperCase()} - Live Tick Data</h3>
            <div ref={chartContainerRef} />
        </div>
    );
}
