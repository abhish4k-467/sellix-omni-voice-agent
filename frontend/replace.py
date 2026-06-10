with open('src/App.tsx', 'r', encoding='utf-8') as f:
    text = f.read()

text = text.replace("import { Mic, Send } from 'lucide-react';", "import { Mic, Send } from 'lucide-react';\nimport { motion, AnimatePresence } from 'framer-motion';")

text = text.replace('{currentPage === "landing" && (\n        <div className="fixed inset-0 z-0 opacity-55">', '<AnimatePresence mode="wait">\n        {currentPage === "landing" && (\n          <motion.div\n            key="shader"\n            initial={{ opacity: 0 }}\n            animate={{ opacity: 1 }}\n            exit={{ opacity: 0 }}\n            transition={{ duration: 0.8 }}\n            className="fixed inset-0 z-0 opacity-55"\n          >')
text = text.replace('<ShaderAnimation />\n        </div>\n      )}', '<ShaderAnimation />\n          </motion.div>\n        )}\n      </AnimatePresence>')

text = text.replace('{currentPage === "landing" ? (\n        <div className="relative z-10 flex flex-col min-h-screen animate-fade-in">', '<AnimatePresence mode="wait">\n        {currentPage === "landing" ? (\n          <motion.div \n            key="landing-page"\n            initial={{ opacity: 0, filter: "blur(4px)" }}\n            animate={{ opacity: 1, filter: "blur(0px)" }}\n            exit={{ opacity: 0, filter: "blur(4px)" }}\n            transition={{ duration: 0.7, ease: "easeInOut" }}\n            className="relative z-10 flex flex-col min-h-screen">')

text = text.replace('          </div>\n        ) : (\n          <div className="relative z-10 flex flex-col items-center justify-center min-h-screen text-center animate-fade-in bg-black p-4">', '          </motion.div>\n        ) : (\n          <motion.div\n            key="loading-page"\n            initial={{ opacity: 0, filter: "blur(4px)" }}\n            animate={{ opacity: 1, filter: "blur(0px)" }}\n            exit={{ opacity: 0, filter: "blur(4px)" }}\n            transition={{ duration: 0.7, ease: "easeInOut" }}\n            className="relative z-10 flex flex-col items-center justify-center min-h-screen text-center bg-black p-4"\n          >')

text = text.replace('        </div>\n      )}\n    </div>\n  );\n}', '          </motion.div>\n        )}\n        </AnimatePresence>\n      </div>\n    </div>\n  );\n}')

with open('src/App.tsx', 'w', encoding='utf-8') as f:
    f.write(text)
