import { createContext, useContext } from 'react';

export const ModelCatalogContext = createContext(null);
export const useModelCatalog = () => useContext(ModelCatalogContext);
