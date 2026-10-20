// SPDX-License-Identifier: MIT
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { applyAccent, storedAccent } from './lib/color'
import './index.css'

applyAccent(storedAccent())

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
