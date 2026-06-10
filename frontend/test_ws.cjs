const WebSocket = require('ws');
const ws = new WebSocket('ws://localhost:8000/ws');

ws.on('open', function open() {
  console.log('connected');
  // Send 10 chunks of 1024 float32 samples (16kHz)
  const chunk = new Float32Array(1024);
  // Add some fake speaking noise (sine wave)
  for (let i = 0; i < 1024; i++) {
    chunk[i] = Math.sin(i * 0.1) * 0.5;
  }
  const chunkBytes = Buffer.from(chunk.buffer, chunk.byteOffset, chunk.byteLength);
  
  for (let i = 0; i < 10; i++) {
    ws.send(chunkBytes);
  }
  
  // Then send silence chunks to trigger 'patience'
  const silentChunk = new Float32Array(1024);
  const silentBytes = Buffer.from(silentChunk.buffer, silentChunk.byteOffset, silentChunk.byteLength);
  console.log('Sending silence...');
  for (let i = 0; i < 20; i++) {
    ws.send(silentBytes);
  }
  console.log('Sent data.');
});

ws.on('message', function incoming(data) {
  console.log('Received message of length:', data.length);
});

ws.on('close', () => {
    console.log('Disconnected');
});
ws.on('error', (e) => {
    console.log('Error', e);
});
