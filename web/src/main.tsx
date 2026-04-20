import { gotoVSCode, Inspector } from 'react-dev-inspector';
import ReactDOM from 'react-dom/client';
import '../tailwind.css';
import App from './app';
import './global.less';
import { initLanguage } from './locales/config';
import { consumeBridgeToken } from './utils/bridge-handoff';

Promise.all([initLanguage(), consumeBridgeToken()]).then(() => {
  ReactDOM.createRoot(document.getElementById('root')!).render(
    <>
      <Inspector keys={['alt', 'c']} onInspectElement={gotoVSCode} />
      <App />
    </>,
  );
});
