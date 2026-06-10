import os

os.makedirs('src/components/ui', exist_ok=True)

with open('src/components/ui/login-form.tsx', 'w', encoding='utf-8') as f:
    f.write('''"use client";
import React, { useEffect, useRef, useState } from "react";
import { User, Lock, ArrowRight } from 'lucide-react';

const vertexSmokeySource = `
  attribute vec4 a_position;
  void main() {
    gl_Position = a_position;
  }
`;

const fragmentSmokeySource = `
precision mediump float;

uniform vec2 iResolution;
uniform float iTime;
uniform vec2 iMouse;
uniform vec3 u_color;

void mainImage(out vec4 fragColor, in vec2 fragCoord){
    vec2 uv = fragCoord / iResolution;
    vec2 centeredUV = (2.0 * fragCoord - iResolution.xy) / min(iResolution.x, iResolution.y);

    float time = iTime * 0.5;

    vec2 mouse = iMouse / iResolution;
    vec2 rippleCenter = 2.0 * mouse - 1.0;

    vec2 distortion = centeredUV;
    for (float i = 1.0; i < 8.0; i++) {
        distortion.x += 0.5 / i * cos(i * 2.0 * distortion.y + time + rippleCenter.x * 3.1415);
        distortion.y += 0.5 / i * cos(i * 2.0 * distortion.x + time + rippleCenter.y * 3.1415);
    }

    float wave = abs(sin(distortion.x + distortion.y + time));
    float glow = smoothstep(0.9, 0.2, wave);

    fragColor = vec4(u_color * glow, 1.0);
}

void main() {
    mainImage(gl_FragColor, gl_FragCoord.xy);
}
`;

type BlurSize = "none" | "sm" | "md" | "lg" | "xl" | "2xl" | "3xl";

interface SmokeyBackgroundProps {
  backdropBlurAmount?: string;
  color?: string;
  className?: string;
}

const blurClassMap: Record<BlurSize, string> = {
  none: "backdrop-blur-none",
  sm: "backdrop-blur-sm",
  md: "backdrop-blur-md",
  lg: "backdrop-blur-lg",
  xl: "backdrop-blur-xl",
  "2xl": "backdrop-blur-2xl",
  "3xl": "backdrop-blur-3xl",
};

export function SmokeyBackground({
  backdropBlurAmount = "sm",
  color = "#1E40AF",
  className = "",
}: SmokeyBackgroundProps): JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [mousePosition, setMousePosition] = useState({ x: 0, y: 0 });
  const [isHovering, setIsHovering] = useState(false);

  const hexToRgb = (hex: string): [number, number, number] => {
    const r = parseInt(hex.substring(1, 3), 16) / 255;
    const g = parseInt(hex.substring(3, 5), 16) / 255;
    const b = parseInt(hex.substring(5, 7), 16) / 255;
    return [r, g, b];
  };

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const gl = canvas.getContext("webgl");
    if (!gl) return;

    const compileShader = (type: number, source: string): WebGLShader | null => {
      const shader = gl.createShader(type);
      if (!shader) return null;
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
        gl.deleteShader(shader);
        return null;
      }
      return shader;
    };

    const vertexShader = compileShader(gl.VERTEX_SHADER, vertexSmokeySource);
    const fragmentShader = compileShader(gl.FRAGMENT_SHADER, fragmentSmokeySource);
    if (!vertexShader || !fragmentShader) return;

    const program = gl.createProgram();
    if (!program) return;
    gl.attachShader(program, vertexShader);
    gl.attachShader(program, fragmentShader);
    gl.linkProgram(program);

    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return;

    gl.useProgram(program);

    const positionBuffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]), gl.STATIC_DRAW);

    const positionLocation = gl.getAttribLocation(program, "a_position");
    gl.enableVertexAttribArray(positionLocation);
    gl.vertexAttribPointer(positionLocation, 2, gl.FLOAT, false, 0, 0);

    const iResolutionLocation = gl.getUniformLocation(program, "iResolution");
    const iTimeLocation = gl.getUniformLocation(program, "iTime");
    const iMouseLocation = gl.getUniformLocation(program, "iMouse");
    const uColorLocation = gl.getUniformLocation(program, "u_color");

    let startTime = Date.now();
    const [r, g, b] = hexToRgb(color);
    gl.uniform3f(uColorLocation, r, g, b);

    let animationFrameId: number;

    const render = () => {
      const width = canvas.clientWidth;
      const height = canvas.clientHeight;
      if (canvas.width !== width || canvas.height !== height) {
          canvas.width = width;
          canvas.height = height;
          gl.viewport(0, 0, width, height);
      }

      const currentTime = (Date.now() - startTime) / 1000;

      gl.uniform2f(iResolutionLocation, width, height);
      gl.uniform1f(iTimeLocation, currentTime);
      gl.uniform2f(iMouseLocation, isHovering ? mousePosition.x : width / 2, isHovering ? height - mousePosition.y : height / 2);

      gl.drawArrays(gl.TRIANGLES, 0, 6);
      animationFrameId = requestAnimationFrame(render);
    };

    const handleMouseMove = (event: globalThis.MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      setMousePosition({ x: event.clientX - rect.left, y: event.clientY - rect.top });
    };
    const handleMouseEnter = () => setIsHovering(true);
    const handleMouseLeave = () => setIsHovering(false);

    canvas.addEventListener("mousemove", handleMouseMove);
    canvas.addEventListener("mouseenter", handleMouseEnter);
    canvas.addEventListener("mouseleave", handleMouseLeave);

    render();

    return () => {
      cancelAnimationFrame(animationFrameId);
      canvas.removeEventListener("mousemove", handleMouseMove);
      canvas.removeEventListener("mouseenter", handleMouseEnter);
      canvas.removeEventListener("mouseleave", handleMouseLeave);
    };
  }, [isHovering, mousePosition, color]);

  const finalBlurClass = blurClassMap[backdropBlurAmount as BlurSize] || blurClassMap["sm"];

  return (
    <div className={`absolute inset-0 w-full h-full overflow-hidden ${className}`}>
      <canvas ref={canvasRef} className="w-full h-full" />
      <div className={`absolute inset-0 ${finalBlurClass}`}></div>
    </div>
  );
}

export function LoginForm() {
  return (
    <div className="w-full max-w-md p-8 space-y-6 bg-white/10 backdrop-blur-lg rounded-2xl border border-white/20 shadow-2xl">
      <div className="text-center">
        <div className="text-4xl md:text-5xl font-bold font-syne tracking-tight text-white mb-2">Sellix</div>
        <h2 className="text-2xl font-bold text-white">Welcome Back</h2>
        <p className="mt-2 text-sm text-gray-300">Sign in to continue</p>
      </div>
      <form className="space-y-8" onSubmit={(e) => { e.preventDefault(); }}>
        <div className="relative z-0">
          <input
            type="email"
            id="floating_email"
            className="block py-2.5 px-0 w-full text-base text-white bg-transparent border-0 border-b-2 border-gray-300 appearance-none focus:outline-none focus:ring-0 focus:border-[#00f0ff] peer"
            placeholder=" " 
            required
          />
          <label
            htmlFor="floating_email"
            className="absolute text-sm text-gray-300 duration-300 transform -translate-y-6 scale-75 top-3 -z-10 origin-[0] peer-focus:left-0 peer-focus:text-[#00f0ff] peer-placeholder-shown:scale-100 peer-placeholder-shown:translate-y-0 peer-focus:scale-75 peer-focus:-translate-y-6"
          >
            <User className="inline-block mr-2 -mt-1" size={16} />
            Email Address
          </label>
        </div>
        <div className="relative z-0">
          <input
            type="password"
            id="floating_password"
            className="block py-2.5 px-0 w-full text-base text-white bg-transparent border-0 border-b-2 border-gray-300 appearance-none focus:outline-none focus:ring-0 focus:border-[#00f0ff] peer"
            placeholder=" "
            required
          />
          <label
            htmlFor="floating_password"
            className="absolute text-sm text-gray-300 duration-300 transform -translate-y-6 scale-75 top-3 -z-10 origin-[0] peer-focus:left-0 peer-focus:text-[#00f0ff] peer-placeholder-shown:scale-100 peer-placeholder-shown:translate-y-0 peer-focus:scale-75 peer-focus:-translate-y-6"
          >
            <Lock className="inline-block mr-2 -mt-1" size={16} />
            Password
          </label>
        </div>

        <div className="flex items-center justify-between">
          <a href="#" className="text-xs text-gray-300 hover:text-white transition">Forgot Password?</a>
        </div>
        
        <button
          type="submit"
          className="group w-full flex items-center justify-center py-3 px-4 bg-gradient-to-r from-[#00f0ff] to-[#0080ff] hover:opacity-90 rounded-lg text-white font-semibold focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-gray-900 focus:ring-[#00f0ff] transition-all duration-300"
        >
          Sign In
          <ArrowRight className="ml-2 h-5 w-5 transform group-hover:translate-x-1 transition-transform" />
        </button>

        <div className="relative flex py-2 items-center">
            <div className="flex-grow border-t border-gray-400/30"></div>
            <span className="flex-shrink mx-4 text-gray-400 text-xs">OR CONTINUE WITH</span>
            <div className="flex-grow border-t border-gray-400/30"></div>
        </div>

        <button
          type="button"
          className="w-full flex items-center justify-center py-2.5 px-4 bg-white/90 hover:bg-white rounded-lg text-gray-900 font-semibold focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-gray-900 focus:ring-gray-300 transition-all duration-300"
        >
          <svg className="w-5 h-5 mr-2" viewBox="0 0 48 48">
            <path fill="#FFC107" d="M43.611 20.083H42V20H24v8h11.303c-1.649 4.657-6.08 8-11.303 8c-6.627 0-12-5.373-12-12s5.373-12 12-12c3.059 0 5.842 1.154 7.961 3.039L38.802 8.841C34.553 4.806 29.613 2.5 24 2.5C11.983 2.5 2.5 11.983 2.5 24s9.483 21.5 21.5 21.5S45.5 36.017 45.5 24c0-1.538-.135-3.022-.389-4.417z"></path><path fill="#FF3D00" d="M6.306 14.691l6.571 4.819C14.655 15.108 18.961 12.5 24 12.5c3.059 0 5.842 1.154 7.961 3.039l5.839-5.841C34.553 4.806 29.613 2.5 24 2.5C16.318 2.5 9.642 6.723 6.306 14.691z"></path><path fill="#4CAF50" d="M24 45.5c5.613 0 10.553-2.306 14.802-6.341l-5.839-5.841C30.842 35.846 27.059 38 24 38c-5.039 0-9.345-2.608-11.124-6.481l-6.571 4.819C9.642 41.277 16.318 45.5 24 45.5z"></path><path fill="#1976D2" d="M43.611 20.083H42V20H24v8h11.303c-.792 2.237-2.231 4.166-4.087 5.571l5.839 5.841C44.196 35.123 45.5 29.837 45.5 24c0-1.538-.135-3.022-.389-4.417z"></path>
          </svg>
          Sign in with Google
        </button>
      </form>
       <p className="text-center text-xs text-gray-400">
        Don't have an account? <a href="#" className="font-semibold text-[#00f0ff] hover:text-white transition">Sign Up</a>
      </p>
    </div>
  );
}
''')

with open('src/App.tsx', 'r', encoding='utf-8') as f:
    text = f.read()

import_str = "import { motion, AnimatePresence } from 'framer-motion';"
replacement_import = "import { motion, AnimatePresence } from 'framer-motion';\nimport { SmokeyBackground, LoginForm } from './components/ui/login-form';"
if replacement_import not in text:
    text = text.replace(import_str, replacement_import)

if 'useState<"landing" | "loading" | "login">' not in text:
    text = text.replace('useState<"landing" | "loading">', 'useState<"landing" | "loading" | "login">')

text = text.replace('onClick={() => setCurrentPage("loading")} className="text-white">\\n              Get Access', 'onClick={() => setCurrentPage("login")} className="text-white">\\n              Get Access')
text = text.replace('onClick={() => setCurrentPage("loading")} className="text-white">\n              Get Access', 'onClick={() => setCurrentPage("login")} className="text-white">\n              Get Access')

text = text.replace('{currentPage === "landing" ? (', '{currentPage === "landing" && (')

mid_replace = '''          </motion.div>
        ) : (
          <motion.div
            key="loading-page"'''
mid_replacement = '''          </motion.div>
        )}
        {currentPage === "loading" && (
          <motion.div
            key="loading-page"'''
text = text.replace(mid_replace, mid_replacement)

end_replace = '''        )}
      </AnimatePresence>'''

end_replacement = '''        )}
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
      </AnimatePresence>'''
text = text.replace(end_replace, end_replacement)

with open('src/App.tsx', 'w', encoding='utf-8') as f:
    f.write(text)
