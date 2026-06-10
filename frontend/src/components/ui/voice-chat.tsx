import React, { useState, useEffect, useRef, useCallback } from 'react';
import { motion } from 'framer-motion';
import { Mic, MicOff, Settings2, BarChart2, Square, FileText } from 'lucide-react';

declare global {
  interface Window {
    googleAccessToken?: string;
    google?: any;
  }
}

const getAudioContext = (sampleRate?: number) => {
  const AudioContextCtor = window.AudioContext || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AudioContextCtor) {
    throw new Error('AudioContext is not supported in this browser.');
  }
  return sampleRate ? new AudioContextCtor({ sampleRate }) : new AudioContextCtor();
};

const getVoiceSocketUrl = () => {
  const envUrl = import.meta.env.VITE_VOICE_WS_URL;
  if (envUrl) {
    return envUrl;
  }

  const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const isLocalHost =
    window.location.hostname === 'localhost' ||
    window.location.hostname === '127.0.0.1';

  if (isLocalHost) {
    return `${wsProtocol}//${window.location.hostname}:8000/ws`;
  }

  // For mobile/dev tunnels use same origin and route via frontend proxy.
  return `${wsProtocol}//${window.location.host}/ws`;
};

const getApiBaseUrl = () => {
  const envUrl = import.meta.env.VITE_API_BASE_URL;
  if (envUrl) {
    return envUrl;
  }

  const isLocalHost =
    window.location.hostname === 'localhost' ||
    window.location.hostname === '127.0.0.1';

  if (isLocalHost) {
    return `${window.location.protocol}//${window.location.hostname}:8000`;
  }

  return `${window.location.protocol}//${window.location.host}`;
};

export function VoiceChat() {
  const [isRecording, setIsRecording] = useState(false);
  const [selectedModel, setSelectedModel] = useState('prisik-04-2026');
  const [selectedVoice, setSelectedVoice] = useState('Kiki');
  const selectedModelRef = useRef(selectedModel);
  const selectedVoiceRef = useRef(selectedVoice);
  
  useEffect(() => {
    selectedModelRef.current = selectedModel;
  }, [selectedModel]);

  useEffect(() => {
    selectedVoiceRef.current = selectedVoice;
  }, [selectedVoice]);
  const [showSettings, setShowSettings] = useState(false);
  const [status, setStatus] = useState('Idle');
  const [showLiveTranscript, setShowLiveTranscript] = useState(false);
  const [liveSummary, setLiveSummary] = useState('Click Real-time Transcript to start live updates.');
  const [liveCallStatus, setLiveCallStatus] = useState('none');
  const [liveUpdatedAt, setLiveUpdatedAt] = useState('');
  const [isLiveLoading, setIsLiveLoading] = useState(false);
  const [showBetaModal, setShowBetaModal] = useState(true);
  const [isCalendarConnected, setIsCalendarConnected] = useState(() => {
    const storedToken = localStorage.getItem('google_access_token');
    const expiry = localStorage.getItem('google_token_expiry');
    if (storedToken && expiry && Date.now() < parseInt(expiry, 10)) {
      window.googleAccessToken = storedToken;
      return true;
    }
    return false;
  });

  const wsRef = useRef<WebSocket | null>(null);
  const wsConnectPromiseRef = useRef<Promise<WebSocket> | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const mediaSourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const monitorGainRef = useRef<GainNode | null>(null);
  const playbackContextRef = useRef<AudioContext | null>(null);
  const playbackAnalyserRef = useRef<AnalyserNode | null>(null);
  const nextPlayTimeRef = useRef<number>(0);
  const playbackSourcesRef = useRef<Set<AudioBufferSourceNode>>(new Set());

  // Initialize playback AudioContext
  useEffect(() => {
    const ctx = getAudioContext();
    playbackContextRef.current = ctx;
    
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 256;
    analyser.connect(ctx.destination);
    playbackAnalyserRef.current = analyser;

    // Dispatch intensity event loop
    let requestRef: number;
    const dataArray = new Uint8Array(analyser.frequencyBinCount);
    
    const updateIntensity = () => {
      if (playbackSourcesRef.current.size > 0 && ctx.state === 'running') {
        analyser.getByteFrequencyData(dataArray);
        let sum = 0;
        for (let i = 0; i < dataArray.length; i++) {
          sum += dataArray[i] || 0;
        }
        const avg = sum / dataArray.length;
        const normalized = Math.min(1, avg / 128); // 0 to 1
        window.dispatchEvent(new CustomEvent('agent-audio-intensity', { detail: normalized }));
      } else {
        window.dispatchEvent(new CustomEvent('agent-audio-intensity', { detail: 0 }));
      }
      requestRef = requestAnimationFrame(updateIntensity);
    };
    
    requestRef = requestAnimationFrame(updateIntensity);

    return () => {
      cancelAnimationFrame(requestRef);
      ctx.close();
    };
  }, []);

  const clearPlaybackQueue = useCallback(() => {
    nextPlayTimeRef.current = playbackContextRef.current?.currentTime || 0;

    playbackSourcesRef.current.forEach((source) => {
      try {
        source.stop();
      } catch {
        // Ignore sources that already finished.
      }
    });
    playbackSourcesRef.current.clear();
  }, []);

  const fetchLiveTranscriptSummary = useCallback(async () => {
    setIsLiveLoading(true);
    try {
      const response = await fetch(`${getApiBaseUrl()}/api/ringg/live-transcript-summary`);
      const data = await response.json();
      if (!response.ok || !data?.ok) {
        throw new Error('Failed to fetch live transcript summary');
      }

      setLiveCallStatus(String(data.status || 'none'));
      setLiveSummary(String(data.summary || 'No summary available yet.'));
      setLiveUpdatedAt(String(data.updated_at || ''));
    } catch (err) {
      console.error('Live transcript summary error:', err);
      setLiveSummary('Could not fetch live transcript summary right now.');
      setLiveCallStatus('error');
    } finally {
      setIsLiveLoading(false);
    }
  }, []);

  const connectWebSocket = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return Promise.resolve(wsRef.current);
    }

    if (wsConnectPromiseRef.current) {
      return wsConnectPromiseRef.current;
    }

    setStatus('Connecting...');
    const ws = new WebSocket(getVoiceSocketUrl());
    ws.binaryType = "arraybuffer";

    wsConnectPromiseRef.current = new Promise((resolve, reject) => {
      ws.onopen = () => {
        console.log('Connected to Voice Agent WS');
        setStatus(current => current === 'Connecting...' ? 'Connected' : current);
        wsConnectPromiseRef.current = null;
        resolve(ws);
        
        ws.send(JSON.stringify({ type: 'config', voice: selectedVoiceRef.current }));
        ws.send(JSON.stringify({ type: 'config', model: selectedModelRef.current }));

        const storedToken = localStorage.getItem('google_access_token');
        const expiry = localStorage.getItem('google_token_expiry');
        if (storedToken && expiry && Date.now() < parseInt(expiry, 10)) {
           window.googleAccessToken = storedToken;
           setIsCalendarConnected(true);
        } else if (storedToken) {
           localStorage.removeItem('google_access_token');
           localStorage.removeItem('google_token_expiry');
           setIsCalendarConnected(false);
        }

        // Ensure google token is sent on reconnect if available
        if (window.googleAccessToken) {
           ws.send(JSON.stringify({ type: 'config', google_token: window.googleAccessToken }));
        }

        // Fetch browser location and send to backend
        if ("geolocation" in navigator) {
          navigator.geolocation.getCurrentPosition(
            async (position) => {
              try {
                const lat = position.coords.latitude;
                const lon = position.coords.longitude;
                // Reverse geocode via Nominatim
                const res = await fetch(`https://nominatim.openstreetmap.org/reverse?format=json&lat=${lat}&lon=${lon}`);
                const data = await res.json();
                
                // Get the most relevant local area name
                const address = data.address || {};
                const localArea = address.city || address.town || address.village || address.suburb || address.county || data.display_name || `${lat}, ${lon}`;
                
                if (ws.readyState === WebSocket.OPEN) {
                  ws.send(JSON.stringify({ type: 'config', location: localArea }));
                }
              } catch (err) {
                // Ignore API failures; fallback to raw coords
                const locStr = `${position.coords.latitude}, ${position.coords.longitude}`;
                if (ws.readyState === WebSocket.OPEN) {
                  ws.send(JSON.stringify({ type: 'config', location: locStr }));
                }
              }
            },
            (err) => console.log("Geolocation error:", err),
            { timeout: 10000 }
          );
        }
      };

      ws.onerror = (event) => {
        console.error('WS Error:', event);
        setStatus('Connection Error');
        wsConnectPromiseRef.current = null;
        reject(new Error('WebSocket connection failed'));
      };

      ws.onmessage = async (event) => {
        if (typeof event.data === 'string') {
          if (event.data === 'interrupt') {
            console.log("Agent playback interrupted!");
            clearPlaybackQueue();
          }
          return;
        }

        if (event.data instanceof ArrayBuffer && playbackContextRef.current) {
          try {
            const ctx = playbackContextRef.current;
            const intArray = new Int16Array(event.data);
            if (intArray.length === 0) return;
            
            const floatArray = new Float32Array(intArray.length);
            for (let i = 0; i < intArray.length; i++) {
              floatArray[i] = (intArray[i] ?? 0) / 32768.0;
            }

            const audioBuffer = ctx.createBuffer(1, floatArray.length, 24000);
            audioBuffer.getChannelData(0).set(floatArray);

            const source = ctx.createBufferSource();
            source.buffer = audioBuffer;
            if (playbackAnalyserRef.current) {
              source.connect(playbackAnalyserRef.current);
            } else {
              source.connect(ctx.destination);
            }
            playbackSourcesRef.current.add(source);
            source.onended = () => {
              playbackSourcesRef.current.delete(source);
            };

            const currentTime = ctx.currentTime;
            if (nextPlayTimeRef.current < currentTime) {
              nextPlayTimeRef.current = currentTime;
            }

            source.start(nextPlayTimeRef.current);
            nextPlayTimeRef.current += audioBuffer.duration;
          } catch (err) {
            console.error('Error playing audio chunk', err);
          }
        }
      };
    });

    ws.onclose = () => {
      console.log('WS Closed');
      wsConnectPromiseRef.current = null;
      wsRef.current = null;
      clearPlaybackQueue();
      setStatus('Disconnected');
      setIsRecording(false);
    };

    wsRef.current = ws;
    return wsConnectPromiseRef.current;
  }, [clearPlaybackQueue]);

  const stopRecording = useCallback(() => {
    setIsRecording(false);
    setStatus(wsRef.current?.readyState === WebSocket.OPEN ? 'Connected' : 'Idle');
    
    processorRef.current?.disconnect();
    processorRef.current = null;

    mediaSourceRef.current?.disconnect();
    mediaSourceRef.current = null;

    monitorGainRef.current?.disconnect();
    monitorGainRef.current = null;

    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach(track => track.stop());
      mediaStreamRef.current = null;
    }

    if (audioContextRef.current) {
      audioContextRef.current.close();
      audioContextRef.current = null;
    }
  }, []);

  const startRecording = async () => {
    try {
      const ws = await connectWebSocket();

      if (playbackContextRef.current?.state === 'suspended') {
        await playbackContextRef.current.resume();
      }

      audioContextRef.current = getAudioContext(16000);

      const stream = await navigator.mediaDevices.getUserMedia({ 
        audio: {
          sampleRate: 16000,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true
        } 
      });
      mediaStreamRef.current = stream;

      const source = audioContextRef.current.createMediaStreamSource(stream);
      mediaSourceRef.current = source;
      const processor = audioContextRef.current.createScriptProcessor(1024, 1, 1);
      const silentMonitor = audioContextRef.current.createGain();
      silentMonitor.gain.value = 0;
      monitorGainRef.current = silentMonitor;
      
      processor.onaudioprocess = (e) => {
        if (ws.readyState === WebSocket.OPEN) {
          const inputData = e.inputBuffer.getChannelData(0);
          ws.send(inputData.slice().buffer);
        }
      };

      source.connect(processor);
      processor.connect(silentMonitor);
      silentMonitor.connect(audioContextRef.current.destination);
      processorRef.current = processor;

      setIsRecording(true);
      setStatus('Listening...');
    } catch (err) {
      console.error('Failed to start recording:', err);
      stopRecording();
      setStatus('Microphone / Connection Error');
    }
  };

  const toggleRecording = () => {
    if (isRecording) {
      stopRecording();
    } else {
      startRecording();
    }
  };

  useEffect(() => {
    connectWebSocket().catch(() => null);
    return () => {
      clearPlaybackQueue();
      stopRecording();
      wsRef.current?.close();
    };
  }, [clearPlaybackQueue, connectWebSocket, stopRecording]);

  useEffect(() => {
    if (!showLiveTranscript) {
      return;
    }

    fetchLiveTranscriptSummary();
    const id = window.setInterval(() => {
      fetchLiveTranscriptSummary();
    }, 5000);

    return () => {
      window.clearInterval(id);
    };
  }, [showLiveTranscript, fetchLiveTranscriptSummary]);

  return (
    <>
      {showBetaModal && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
        >
          <motion.div
            initial={{ scale: 0.95, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.95, opacity: 0 }}
            className="bg-black/80 border border-white/10 rounded-2xl p-8 max-w-md w-full shadow-2xl relative overflow-hidden"
          >
            {/* Subtle glow effect matching the theme */}
            <div className="absolute -top-20 -right-20 w-40 h-40 bg-white/5 rounded-full blur-3xl pointer-events-none" />
            
            <div className="relative z-10 flex flex-col items-center text-center">
              <div className="w-12 h-12 rounded-full bg-white/5 flex items-center justify-center border border-white/10 mb-6">
                <BarChart2 className="w-6 h-6 text-white/70" />
              </div>
              <h2 className="text-2xl font-light text-white tracking-widest mb-3">Beta <span className="font-bold">Caution</span></h2>
              <p className="text-sm text-white/50 leading-relaxed mb-8">
                Sellix is currently in an experimental beta phase. The voice agent may occasionally mispronounce words or experience latency loops. Microphone permissions are required to interact.
              </p>
              <button
                onClick={() => setShowBetaModal(false)}
                className="w-full bg-white/5 hover:bg-white/10 text-white/90 border border-white/10 rounded-xl py-3 px-6 transition-all duration-300 hover:shadow-[0_0_20px_rgba(255,255,255,0.1)] text-sm tracking-widest"
              >
                I UNDERSTAND
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}

      <div className="flex flex-col items-center justify-center min-h-[70vh] w-full max-w-4xl mx-auto p-6 relative z-10">
        {/* Top Bar / Settings */}
        <div className="absolute top-0 right-0 p-4">
        <div className="relative">
          <button
            onClick={() => setShowSettings(!showSettings)}
            className="p-2 rounded-full border border-white/10 bg-black/40 text-white/70 hover:text-white transition-all backdrop-blur-md"
          >
            <Settings2 className="w-5 h-5" />
          </button>
          
          {showSettings && (
            <motion.div
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              className="absolute right-0 mt-3 w-48 bg-black/90 border border-white/10 rounded-xl overflow-hidden backdrop-blur-xl shadow-lg z-50"
            >
              <div className="p-3 border-b border-white/5 bg-white/5">
                <span className="text-xs uppercase font-mono text-white/50 tracking-wider">Model Selection</span>
              </div>
              <div className="flex flex-col p-2 gap-1 border-b border-white/5">
                {['prisik-04-2026', 'prisik-02-2025'].map(model => (
                  <button
                    key={model}
                      onClick={() => {
                        setSelectedModel(model);
                        if (wsRef.current?.readyState === WebSocket.OPEN) {
                          wsRef.current.send(JSON.stringify({ type: 'config', model }));
                        }
                      }}
                    className={`text-left px-3 py-2 text-sm rounded-lg transition-colors font-mono ${
                      selectedModel === model 
                        ? 'bg-cyan-500/10 text-cyan-400' 
                        : 'text-white/60 hover:bg-white/5 hover:text-white'
                    }`}
                  >
                    {model}
                  </button>
                ))}
              </div>

              <div className="p-3 border-b border-white/5 bg-white/5">
                <span className="text-xs uppercase font-mono text-white/50 tracking-wider">Integrations</span>
              </div>
              <div className="flex flex-col p-2 gap-1 border-b border-white/5">
                <button
                  onClick={() => {
                     // @ts-ignore
                     const client = window.google.accounts.oauth2.initTokenClient({
                        client_id: '633184826455-e78hsrfmeehqrmaan06a3vrcjiavrl04.apps.googleusercontent.com',
                        scope: 'https://www.googleapis.com/auth/calendar',
                        callback: (tokenResponse: any) => {
                          if (tokenResponse && tokenResponse.access_token) {
                            // @ts-ignore
                            window.googleAccessToken = tokenResponse.access_token;
                            setIsCalendarConnected(true);
                            
                            // Save to localStorage with an expiry time (usually 3600 seconds)
                            const expiresIn = tokenResponse.expires_in || 3600;
                            const expiryTime = Date.now() + (expiresIn * 1000);
                            localStorage.setItem('google_access_token', tokenResponse.access_token);
                            localStorage.setItem('google_token_expiry', expiryTime.toString());

                            if (wsRef.current?.readyState === WebSocket.OPEN) {
                              wsRef.current.send(JSON.stringify({ type: 'config', google_token: tokenResponse.access_token }));
                            }
                          }
                        },
                     });
                     client.requestAccessToken();
                  }}
                  className={`text-left px-3 py-2 text-sm rounded-lg transition-colors font-mono ${
                    isCalendarConnected 
                      ? 'bg-green-500/10 text-green-400' 
                      : 'text-white/60 hover:bg-white/5 hover:text-white'
                  }`}
                >
                  {isCalendarConnected ? "Calendar Connected" : "Connect Calendar"}
                </button>
              </div>

              <div className="p-3 border-b border-white/5 bg-white/5">
                <span className="text-xs uppercase font-mono text-white/50 tracking-wider">Voice Actor</span>
              </div>
              <div className="flex flex-col p-2 gap-1 max-h-48 overflow-y-auto scrollbar-thin scrollbar-thumb-white/10 scrollbar-track-transparent">
                {['Bella', 'Jasper', 'Luna', 'Bruno', 'Rosie', 'Hugo', 'Kiki', 'Leo'].map(voice => (
                  <button
                    key={voice}
                    onClick={() => {
                      setSelectedVoice(voice);
                      if (wsRef.current?.readyState === WebSocket.OPEN) {
                        wsRef.current.send(JSON.stringify({ type: 'config', voice }));
                      }
                    }}
                    className={`text-left px-3 py-2 text-sm rounded-lg transition-colors font-mono ${
                      selectedVoice === voice 
                        ? 'bg-cyan-500/10 text-cyan-400' 
                        : 'text-white/60 hover:bg-white/5 hover:text-white'
                    }`}
                  >
                    {voice}
                  </button>
                ))}
              </div>
            </motion.div>
          )}
        </div>
      </div>

      {/* Main Agent Interface */}
      <div className="flex flex-col items-center justify-center space-y-12 w-full">
        {/* Simple Text Logo instead of the orb */}
        <div className="relative flex items-center justify-center w-full mx-auto mt-20 mb-8">
          <div className="text-6xl md:text-8xl font-bold font-syne tracking-tight text-white drop-shadow-[0_0_15px_rgba(255,255,255,0.3)]">
            Sellix
          </div>
        </div>

        {/* Info Text */}
        <div className="text-center space-y-2 relative z-10">
          <p className="font-mono text-sm text-white/50 tracking-wider uppercase">
            Current Model: <span className="text-white/90">{selectedModel}</span>
          </p>
          <p className="text-lg text-white/80 max-w-md mx-auto font-light">
            {status}
          </p>
        </div>

        {/* Controls */}
        <div className="flex items-center gap-6 mt-8 relative z-20">
          <button
            onClick={toggleRecording}
            className={`flex items-center justify-center w-20 h-20 rounded-full border transition-all duration-300 ${
              isRecording 
                ? 'border-red-500/50 bg-red-500/10 text-red-400 shadow-[0_0_20px_rgba(239,68,68,0.2)] hover:bg-red-500/20' 
                : 'border-white/20 bg-black/50 text-white/70 hover:border-cyan-400/50 hover:bg-cyan-500/10 hover:text-cyan-400 shadow-lg'
            }`}
          >
            {isRecording ? <MicOff className="w-8 h-8" /> : <Mic className="w-8 h-8" />}
          </button>

          <button
            onClick={() => {
              if (wsRef.current?.readyState === WebSocket.OPEN) {
                wsRef.current.send(JSON.stringify({ type: 'interrupt' }));
              }
              clearPlaybackQueue();
            }}
            className="flex items-center justify-center w-16 h-16 rounded-full border border-white/20 bg-black/50 text-white/70 hover:border-red-500/50 hover:bg-red-500/10 hover:text-red-400 shadow-lg transition-all duration-300"
            title="Stop AI Speaking"
          >
            <Square className="w-6 h-6 fill-current" />
          </button>

          <button
            onClick={() => setShowLiveTranscript((prev) => !prev)}
            className={`flex items-center justify-center gap-2 px-4 h-12 rounded-full border bg-black/50 shadow-lg transition-all duration-300 font-mono text-xs uppercase tracking-wider ${
              showLiveTranscript
                ? 'border-cyan-400/60 text-cyan-300 bg-cyan-500/10'
                : 'border-white/20 text-white/70 hover:border-cyan-400/50 hover:bg-cyan-500/10 hover:text-cyan-300'
            }`}
            title="Toggle real-time transcript summary"
          >
            <FileText className="w-4 h-4" />
            Real-time Transcript
          </button>
        </div>

        {showLiveTranscript && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            className="w-full max-w-2xl mt-8 border border-white/10 bg-black/50 rounded-2xl p-5 backdrop-blur-xl"
          >
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <span className={`inline-block w-2.5 h-2.5 rounded-full ${liveCallStatus === 'ongoing' || liveCallStatus === 'registered' || liveCallStatus === 'retry' ? 'bg-emerald-400' : liveCallStatus === 'error' ? 'bg-red-400' : 'bg-white/40'}`} />
                <span className="text-white/90 font-mono text-xs uppercase tracking-wider">
                  {isLiveLoading ? 'Updating...' : `Status: ${liveCallStatus}`}
                </span>
              </div>
              <span className="text-white/40 text-xs font-mono">
                {liveUpdatedAt ? `Updated ${new Date(liveUpdatedAt).toLocaleTimeString()}` : ''}
              </span>
            </div>
            <p className="text-white/80 leading-relaxed text-sm">
              {liveSummary}
            </p>
          </motion.div>
        )}
      </div>
    </div>
    </>
  );
}
