import { createContext, useContext } from 'react';

const ActiveLocationContext = createContext(null);

export const ActiveLocationProvider = ActiveLocationContext.Provider;

export function useActiveLocation() {
  return useContext(ActiveLocationContext);
}
