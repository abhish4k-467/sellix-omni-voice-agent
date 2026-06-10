import React, { useState, useEffect } from 'react';
import { ShaderAnimation } from './components/ui/shader-animation';
import { LiquidButton } from './components/ui/liquid-glass-button';
import { AwardBadge } from './components/ui/award-badge';
import { Entropy } from './components/ui/entropy';
import { Mic, Send } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { SmokeyBackground, LoginForm } from './components/ui/login-form';
import { ShaderLinesAnimation } from './components/ui/shader-lines';
import { BubbleText } from './components/ui/bubble-text';
import { VoiceChat } from './components/ui/voice-chat';

const AI_FACTS = [
  "Generative AI models possess billions of parameters, learning complex patterns from vast amounts of data.",
  "Agentic AI systems can autonomously plan, execute, and adapt multi-step workflows to achieve abstract goals.",
  "Future physical humanoid robots aim to seamlessly integrate into environments using advanced multimodal AI paradigms.",
  "Neuromorphic computing chips are drastically reducing the energy required for complex AI inferences.",
  "Reinforcement Learning from Human Feedback (RLHF) fundamentally shaped modern LLM behavior and alignment.",
  "Swarm robotics will allow multiple AI agents to collaborate in real-time on massive physical construction tasks."
];

const AnimatedWave = () => {
  return (
    <div className="flex items-center gap-1 h-6">
      {[...Array(5)].map((_, i) => (
        <div
          key={i}
          className="w-1 bg-cyan-400 rounded-full animate-wave"
          style={{
            animationDelay: `${i * 0.15}s`,
            height: '100%',
          }}
        />
      ))}
    </div>
  );
};

export default function App() {
  const [currentPage, setCurrentPage] = useState<"landing" | "loading" | "login" | "chat">("landing");
  const [factIndex, setFactIndex] = useState(0);
  const [bgVariant] = useState(() => Math.random() > 0.5 ? 'original' : 'lines');

  useEffect(() => {
    if (currentPage !== "loading") {
      setFactIndex(0);
      return;
    }

    let j = 0;
    const factInterval = setInterval(() => {
      j = (j + 1) % AI_FACTS.length;
      setFactIndex(j);
    }, 4500);

    return () => {
      clearInterval(factInterval);
    };
  }, [currentPage]);

  return (
    <div className="relative min-h-screen bg-black text-white font-sans overflow-x-hidden selection:bg-cyan-500/30">
      <AnimatePresence mode="wait">
        {currentPage === "landing" && (
          <motion.div
            key="shader"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.8 }}
            className={`fixed inset-0 z-0 ${bgVariant === 'original' ? 'opacity-55' : 'opacity-25'}`}
          >
            {bgVariant === 'original' ? <ShaderAnimation /> : <ShaderLinesAnimation />}
          </motion.div>
        )}
        {currentPage === "login" && (
          <motion.div
            key="login-page"
            initial={{ opacity: 0, filter: "blur(4px)" }}
            animate={{ opacity: 1, filter: "blur(0px)" }}
            exit={{ opacity: 0, filter: "blur(4px)" }}
            transition={{ duration: 0.7, ease: "easeInOut" }}
            className="relative z-10 flex flex-col items-center justify-center min-h-screen text-center p-4 bg-transparent"
          >
            <SmokeyBackground className="fixed inset-0 z-[-1]" />
            <button 
              onClick={() => setCurrentPage("landing")}
              className="absolute top-8 left-8 text-white/50 hover:text-white transition-colors z-50 text-2xl font-bold flex items-center gap-2"
            >
              ← <span className="text-sm font-mono tracking-widest uppercase">Back</span>
            </button>
            <LoginForm />
          </motion.div>
        )}
        {currentPage === "chat" && (
          <motion.div
            key="chat-page"
            initial={{ opacity: 0, filter: "blur(4px)" }}
            animate={{ opacity: 1, filter: "blur(0px)" }}
            exit={{ opacity: 0, filter: "blur(4px)" }}
            transition={{ duration: 0.7, ease: "easeInOut" }}
            className="relative z-10 flex flex-col items-center justify-center min-h-screen w-full bg-transparent"
          >
            <SmokeyBackground className="fixed inset-0 z-[-1]" />
            <div className="absolute top-8 left-8 z-50">
              <button 
                onClick={() => setCurrentPage("landing")}
                className="text-white/50 hover:text-white transition-colors text-2xl font-bold flex items-center gap-2"
              >
                ← <span className="text-sm font-mono tracking-widest uppercase">Back</span>
              </button>
            </div>
            
            <VoiceChat />
          </motion.div>
        )}
      </AnimatePresence>
      <div className="fixed inset-0 z-0 bg-radial-[at_center] from-transparent to-black/90 pointer-events-none" />

      <AnimatePresence mode="wait">
        {currentPage === "landing" && (
          <motion.div 
            key="landing-page"
            initial={{ opacity: 0, filter: "blur(4px)" }}
            animate={{ opacity: 1, filter: "blur(0px)" }}
            exit={{ opacity: 0, filter: "blur(4px)" }}
            transition={{ duration: 0.7, ease: "easeInOut" }}
            className="relative z-10 flex flex-col min-h-screen">
            <nav className="fixed top-5 left-1/2 -translate-x-1/2 w-[90%] max-w-4xl z-50 flex items-center justify-between px-6 py-3 border border-white/10 rounded-full backdrop-blur-xl bg-black/40 shadow-[0_0_6px_rgba(0,0,0,0.03),0_2px_6px_rgba(0,0,0,0.08),inset_3px_3px_0.5px_-3px_rgba(0,0,0,0.9),inset_-3px_-3px_0.5px_-3px_rgba(0,0,0,0.85),inset_1px_1px_1px_-0.5px_rgba(0,0,0,0.6),inset_-1px_-1px_1px_-0.5px_rgba(0,0,0,0.6),inset_0_0_6px_6px_rgba(0,0,0,0.12),inset_0_0_2px_2px_rgba(0,0,0,0.06),0_0_12px_rgba(255,255,255,0.15)] dark:shadow-[0_0_8px_rgba(0,0,0,0.03),0_2px_6px_rgba(0,0,0,0.08),inset_3px_3px_0.5px_-3.5px_rgba(255,255,255,0.09),inset_-3px_-3px_0.5px_-3.5px_rgba(255,255,255,0.85),inset_1px_1px_1px_-0.5px_rgba(255,255,255,0.6),inset_-1px_-1px_1px_-0.5px_rgba(255,255,255,0.6),inset_0_0_6px_6px_rgba(255,255,255,0.12),inset_0_0_2px_2px_rgba(255,255,255,0.06),0_0_12px_rgba(0,0,0,0.15)]">
              <div className="text-xl md:text-2xl font-bold tracking-tight font-syne text-white">Sellix</div>
              <div className="hidden md:flex items-center gap-8 text-white/70 text-xs font-mono uppercase tracking-wider">
                <a href="#" className="hover:text-white transition-colors">Product</a>
                <a href="#" className="hover:text-white transition-colors">Pricing</a>
                <a href="#" className="hover:text-white transition-colors">Docs</a>
                <a href="#" className="hover:text-white transition-colors">Blog</a>
              </div>
              <LiquidButton size="sm" onClick={() => {
                setCurrentPage("loading");
                setTimeout(() => setCurrentPage("chat"), 3000);
              }} className="text-white">
                API Access
              </LiquidButton>
            </nav>

            <main className="flex-grow flex flex-col items-center justify-center px-4 text-center mt-40 mb-16">
              <h1 className="text-6xl md:text-8xl font-bold font-syne tracking-tight leading-tight mb-6">
                Your Sales Team,<br/>
                <BubbleText 
                  text="Powered by Voice" 
                  className="text-transparent bg-clip-text bg-gradient-to-r from-[#00f0ff] to-[#ff3cac]" 
                />
              </h1>
              <p className="max-w-2xl mx-auto text-lg md:text-xl text-white/55 mb-10 leading-relaxed">
                Deploy hyper-realistic 24/7 AI voice agents that qualify leads, close deals, and support your customers automatically.
              </p>
              <div className="group relative">
                <LiquidButton 
                  size="xxl" 
                  onClick={() => {
                    setCurrentPage("loading");
                    setTimeout(() => setCurrentPage("chat"), 3000);
                  }}
                  className="text-lg font-bold text-white bg-black/20"
                >
                  <Mic className="w-5 h-5 group-hover:animate-pulse text-[#00f0ff]" />
                  <span className="ml-3 group-hover:text-white transition-colors">Try Now</span>
                </LiquidButton>
              </div>
              <div className="mt-20">
                <AwardBadge type="product-of-the-day" />
              </div>
            </main>

            <div className="w-full border-y border-white/10 bg-black/40 backdrop-blur-md py-8">
              <div className="max-w-7xl mx-auto px-4 grid grid-cols-2 md:grid-cols-4 gap-8 md:gap-0 divide-x-0 md:divide-x divide-white/10">
                {[
                  { label: "99.1% Call Answer Rate" },
                  { label: "3.2x Faster Resolution" },
                  { label: "24/7 Always On" },
                  { label: "40+ Languages" }
                ].map((stat, i) => (
                  <div key={i} className="flex flex-col items-center justify-center px-4 text-center">
                    <span className="font-mono text-sm md:text-base text-white/80">{stat.label}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="max-w-4xl mx-auto px-4 py-16 text-center flex flex-col items-center">
              <p className="text-sm text-white/50 uppercase tracking-widest font-mono mb-2">
                A product by Tarkshy Consultancy Services
              </p>
              <a 
                href="https://tarkshy.com" 
                target="_blank" 
                rel="noopener noreferrer" 
                className="text-xs text-white/40 hover:text-[#00f0ff] transition-colors tracking-widest font-mono group flex items-center gap-1"
              >
                tarkshy.com
                <span className="opacity-0 group-hover:opacity-100 transition-opacity -mt-0.5">↗</span>
              </a>
            </div>
          </motion.div>
        )}
        {currentPage === "loading" && (
          <motion.div
            key="loading-page"
            initial={{ opacity: 0, filter: "blur(4px)" }}
            animate={{ opacity: 1, filter: "blur(0px)" }}
            exit={{ opacity: 0, filter: "blur(4px)" }}
            transition={{ duration: 0.7, ease: "easeInOut" }}
            className="relative z-10 flex flex-col items-center justify-center min-h-screen text-center bg-black p-4"
          >
            <button 
              onClick={() => setCurrentPage("landing")}
              className="absolute top-8 left-8 text-white/50 hover:text-white transition-colors z-50 text-2xl font-bold flex items-center gap-2"
            >
              ← <span className="text-sm font-mono tracking-widest uppercase">Back</span>
            </button>
            
            <div className="relative w-full flex flex-col flex-1 items-center justify-center">
              <Entropy size={400} className="mb-8" />

              <div className="my-8 min-h-[80px] flex items-center justify-center max-w-2xl px-4">
                <p className="font-mono text-base md:text-lg leading-relaxed italic text-white/80 tracking-wide transition-all duration-500 will-change-transform word-float" key={factIndex}>
                  "{AI_FACTS[factIndex]}"
                </p>
              </div>
            </div>
          </motion.div>
        )}
        {currentPage === "login" && (
          <motion.div
            key="login-page"
            initial={{ opacity: 0, filter: "blur(4px)" }}
            animate={{ opacity: 1, filter: "blur(0px)" }}
            exit={{ opacity: 0, filter: "blur(4px)" }}
            transition={{ duration: 0.7, ease: "easeInOut" }}
            className="relative z-10 flex flex-col items-center justify-center min-h-screen text-center p-4 bg-transparent"
          >
            <SmokeyBackground className="fixed inset-0 z-[-1]" />
            <button 
              onClick={() => setCurrentPage("landing")}
              className="absolute top-8 left-8 text-white/50 hover:text-white transition-colors z-50 text-2xl font-bold flex items-center gap-2"
            >
              ← <span className="text-sm font-mono tracking-widest uppercase">Back</span>
            </button>
            <LoginForm />
          </motion.div>
        )}
        {currentPage === "chat" && (
          <motion.div
            key="chat-page-empty"
          />
        )}
      </AnimatePresence>
    </div>
  );
}
