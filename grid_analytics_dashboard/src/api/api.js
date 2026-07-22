const API_URL = ""

export async function getCurrentStatus(){
    const response = await fetch(
        `${API_URL}/dashboard_functions/status`
    );

    if(!response.ok){
        throw new Error("Failed to fetch status.")
    }

    return await response.json()
}

export async function getCurrentPower(){
    const response = await fetch(
        `${API_URL}/dashboard_functions/power`
    );

    if(!response.ok){
        throw new Error("Failed to fetch power.")
    }
    
    return await response.json()
}

export async function getCurrentEnergy(){
    const response = await fetch(
        `${API_URL}/dashboard_functions/energy`
    );

    if(!response.ok){
        throw new Error("Failed to fetch energy.")
    }

    return await response.json()
}

export async function getWeather(){
    const response = await fetch(
        `${API_URL}/api/weather`
    );

    if(!response.ok){
        throw new Error("Failed to fetch weather features.")
    }

    return await response.json()
}

export async function getLoadsData(){
    const response = await fetch(
        `${API_URL}/dashboard_functions/loads_data`
    );

    if(!response.ok){
        throw new Error("Failed to fetch load data.")
    }

    return await response.json()
}

export async function getLoadsMetaData(){
    const response = await fetch(
        `${API_URL}/dashboard_functions/loads_metadata`
    );

    if(!response.ok){
        throw new Error("Failed to fetch load data.")
    }

    return await response.json()
}

export async function getLoadPower(load_id, period){
    const response = await fetch(
        `${API_URL}/dashboard_functions/graph?load_id=${load_id}&period=${period}`,
        {
            method: "POST"
        }
    );

    if(!response.ok){
        throw new Error("Failed to fetch periodic load power.");
    }

    return await response.json();
}